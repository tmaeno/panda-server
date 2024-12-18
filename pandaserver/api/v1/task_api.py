import datetime
import json
import re
from typing import Any, Dict, List

from pandacommon.pandalogger.LogWrapper import LogWrapper
from pandacommon.pandalogger.PandaLogger import PandaLogger

from pandaserver.api.v1.common import (
    MESSAGE_TASK_ID,
    generate_response,
    get_dn,
    get_fqan,
    has_production_role,
    request_validation,
)
from pandaserver.srvcore.panda_request import PandaRequest
from pandaserver.taskbuffer import PrioUtil
from pandaserver.taskbuffer.TaskBuffer import TaskBuffer

_logger = PandaLogger().getLogger("task_api")

global_task_buffer = None


def init_task_buffer(task_buffer: TaskBuffer) -> None:
    """
    Initialize the task buffer. This method needs to be called before any other method in this module.
    """
    global global_task_buffer
    global_task_buffer = task_buffer


@request_validation(_logger, secure=True, request_method="POST")
def retry(
    req: PandaRequest,
    jedi_task_id: int,
    new_parameters: str = None,
    no_child_retry: bool = None,
    disable_staging_mode: bool = None,
    keep_gshare_priority: bool = None,
) -> Dict[str, Any]:
    """
    Task retry

    Retry a given task. Requires a secure connection without a production role to retry own tasks and with a production role to retry others' tasks.

    API details:
        HTTP Method: POST
        Path: /task/v1/retry

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID
        new_parameters(str): a json string of new parameters the task uses when rerunning
        no_child_retry(bool): if True, the child tasks are not retried
        disable_staging_mode(bool): if True, the task skips staging state and directly goes to subsequent state
        keep_gshare_priority(bool): if True, the task keeps current gshare and priority

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    user = get_dn(req)
    production_role = has_production_role(req)

    # retry with new params
    if new_parameters:
        try:
            # convert to dict
            new_parameters_dict = PrioUtil.decodeJSON(new_parameters)
            # get original params
            task_params = global_task_buffer.getTaskParamsPanda(jedi_task_id)
            task_params_dict = PrioUtil.decodeJSON(task_params)
            # update with new values
            task_params_dict.update(new_parameters_dict)
            task_params = json.dumps(task_params_dict)
            # retry with new params
            ret = global_task_buffer.insertTaskParamsPanda(
                task_params,
                user,
                production_role,
                [],
                properErrorCode=True,
                allowActiveTask=True,
            )
        except Exception as e:
            ret = 1, f"new parameter conversion failed with {str(e)}"
    else:
        # get command qualifier
        qualifier = ""
        for com_key, com_param in [
            ("sole", no_child_retry),
            ("discard", no_child_retry),
            ("staged", disable_staging_mode),
            ("keep", keep_gshare_priority),
        ]:
            if com_param:
                qualifier += f"{com_key} "
        qualifier = qualifier.strip()
        # normal retry
        ret = global_task_buffer.sendCommandTaskPanda(
            jedi_task_id,
            user,
            production_role,
            "retry",
            properErrorCode=True,
            comQualifier=qualifier,
        )
    data, message = ret
    success = data == 0
    return generate_response(success, message, data)


@request_validation(_logger, secure=True, production=True, request_method="POST")
def resume(req: PandaRequest, jedi_task_id: int) -> Dict[str, Any]:
    """
    Task resume

    Resume a given task. This transitions a paused or throttled task back to its previous active state. Resume can also be used to kick a task in staging state to the next state.
    Requires a secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/resume

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    user = get_dn(req)
    is_production_role = has_production_role(req)

    # check jedi_task_id
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    ret = global_task_buffer.sendCommandTaskPanda(jedi_task_id, user, is_production_role, "resume", properErrorCode=True)
    data, message = ret
    success = data == 0
    return generate_response(success, message, data)


@request_validation(_logger, secure=True, production=True, request_method="POST")
def release(req: PandaRequest, jedi_task_id: int) -> Dict[str, Any]:
    """
    Task release

    Release a given task. This triggers the avalanche for tasks in scouting state or dynamically reconfigures the task to skip over the scouting state.
    Requires a secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/release

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    user = get_dn(req)
    is_production_role = has_production_role(req)

    # check jedi_task_id
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    ret = global_task_buffer.sendCommandTaskPanda(jedi_task_id, user, is_production_role, "release", properErrorCode=True)
    data, message = ret
    success = data == 0
    return generate_response(success, message, data)


# reassign task to site/cloud
@request_validation(_logger, secure=True, request_method="POST")
def reassign(req: PandaRequest, jedi_task_id: int, site: str = None, cloud: str = None, nucleus: str = None, soft: bool = None, mode: str = None):
    """
    Task reassign

    Reassign a given task to a site, nucleus or cloud - depending on the parameters. Requires a secure connection.

    API details:
        HTTP Method: POST
        Path: /task/v1/reassign

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID
        site(str, optional): site name
        cloud(str, optional): cloud name
        nucleus(str, optional): nucleus name
        soft(bool, optional): soft reassign
        mode(str, optional): soft/nokill reassign

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """

    # check jedi_task_id
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    user = get_dn(req)
    is_production_role = has_production_role(req)

    # reassign to site, nucleus or cloud
    if site:
        comment = f"site:{site}:y"  # set 'y' to go back to oldStatus immediately
    elif nucleus:
        comment = f"nucleus:{nucleus}:n"
    else:
        comment = f"cloud:{cloud}:n"

    # set additional modes
    if mode == "nokill":
        comment += ":nokill reassign"
    elif mode == "soft" or soft == True:
        comment += ":soft reassign"

    ret = global_task_buffer.sendCommandTaskPanda(
        jedi_task_id,
        user,
        is_production_role,
        "reassign",
        comComment=comment,
        properErrorCode=True,
    )
    data, message = ret
    success = data == 0
    return generate_response(success, message, data)


@request_validation(_logger, secure=True, production=True, request_method="POST")
def pause(req: PandaRequest, jedi_task_id: int) -> Dict[str, Any]:
    """
    Task pause

    Pause a given task. Requires a secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/pause

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """

    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    user = get_dn(req)
    is_production_role = has_production_role(req)

    ret = global_task_buffer.sendCommandTaskPanda(jedi_task_id, user, is_production_role, "pause", properErrorCode=True)
    data, message = ret
    success = data == 0
    return generate_response(success, message, data)


@request_validation(_logger, secure=True, request_method="POST")
def kill(req: PandaRequest, jedi_task_id: int = None, broadcast: bool = False) -> Dict[str, Any]:
    """
    Task kill

    Kill a given task. Requires a secure connection.

    API details:
        HTTP Method: POST
        Path: /task/v1/kill

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID
        broadcast(bool, optional): broadcast kill command to pilots to kill the jobs

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    # check jedi_task_id
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    user = get_dn(req)
    is_production_role = has_production_role(req)

    ret = global_task_buffer.sendCommandTaskPanda(
        jedi_task_id,
        user,
        is_production_role,
        "kill",
        properErrorCode=True,
        broadcast=broadcast,
    )
    data, message = ret
    success = data == 0
    return generate_response(success, message, data)


@request_validation(_logger, secure=True, request_method="POST")
def finish(req: PandaRequest, jedi_task_id: int, soft: bool = False, broadcast: bool = False) -> Dict[str, Any]:
    """
    Task finish

    Finish a given task. Requires a secure connection.

    API details:
        HTTP Method: POST
        Path: /task/v1/finish

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID
        soft(bool, optional): soft finish
        broadcast(bool, optional): broadcast finish command to pilots

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    qualifier = None
    if soft:
        qualifier = "soft"

    user = get_dn(req)
    is_production_role = has_production_role(req)

    # check jedi_task_id
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    ret = global_task_buffer.sendCommandTaskPanda(
        jedi_task_id,
        user,
        is_production_role,
        "finish",
        properErrorCode=True,
        comQualifier=qualifier,
        broadcast=broadcast,
    )
    data, message = ret
    success = data == 0
    return generate_response(success, message, data)


@request_validation(_logger, secure=True, production=True, request_method="POST")
def reactivate(req: PandaRequest, jedi_task_id: int, keep_attempt_nr: bool = False, trigger_job_generation: bool = False) -> Dict[str, Any]:
    """
    Reactivate task

    Reactivate a given task, i.e. recycle a finished/done task. A reactivated task will generate new jobs and then go to done/finished.
    Requires a secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/reactivate

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID
        keep_attempt_nr(bool, optional): keep the original attempt number
        trigger_job_generation(bool, optional): trigger the job generation

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    ret = global_task_buffer.reactivateTask(jedi_task_id, keep_attempt_nr, trigger_job_generation)
    code, message = ret
    success = code == 0
    return generate_response(success, message)


@request_validation(_logger, secure=True, production=True, request_method="POST")
def avalanche(req: PandaRequest, jedi_task_id: int) -> Dict[str, Any]:
    """
    Task avalanche

    Avalanche a given task. Requires a secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/avalanche

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    user = get_dn(req)
    is_production_role = has_production_role(req)

    # check jedi_task_id
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    ret = global_task_buffer.sendCommandTaskPanda(jedi_task_id, user, is_production_role, "avalanche", properErrorCode=True)
    data, message = ret
    success = data == 0
    return generate_response(success, message, data)


@request_validation(_logger, secure=True, production=True, request_method="POST")
def reassign_global_share(req: PandaRequest, jedi_task_id_list: List, share: str, reassign_running_jobs: bool) -> Dict[str, Any]:
    """
    Reassign the global share of a task

    Reassign the global share of a task. Requires a secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/reassign_global_share

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id_list(List): List of JEDI task IDs to reassign
        share(str): destination share
        reassign_running_jobs(bool): whether you want to reassign existing running jobs

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    _logger.debug(f"reassignShare: jedi_task_ids: {jedi_task_id_list}, share: {share}, reassign_running: {reassign_running_jobs}")

    if not isinstance(jedi_task_id_list, list) or not isinstance(share, str):
        return generate_response(False, message="wrong parameters: jedi_task_ids must be list and share must be string")

    code, message = global_task_buffer.reassignShare(jedi_task_id_list, share, reassign_running_jobs)
    success = code == 0
    return generate_response(success, message, code)


@request_validation(_logger, secure=True, production=True, request_method="POST")
def enable_job_cloning(
    req: PandaRequest,
    jedi_task_id: int,
    mode: str = None,
    multiplicity: int = None,
    num_sites: int = None,
) -> Dict[str, Any]:
    """
    Enable job cloning

    Enable job cloning for a given task. Requires secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/enable_job_cloning

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID
        mode(str): mode of operation, runonce or storeonce
        multiplicity(int): number of clones to be created for each target
        num_sites(int): number of sites to be used for each target

    Returns:
        dict: The system response. True for success, False for failure, and an error message.
    """
    tmp_logger = LogWrapper(_logger, f"enable_job_cloning jediTaskID=={jedi_task_id}")
    tmp_logger.debug(f"Start")
    success, message = global_task_buffer.enable_job_cloning(jedi_task_id, mode, multiplicity, num_sites)
    tmp_logger.debug(f"Done")
    return generate_response(success, message)


@request_validation(_logger, secure=True, production=True, request_method="POST")
def disable_job_cloning(
    req: PandaRequest,
    jedi_task_id: int,
) -> Dict[str, Any]:
    """
    Disable job cloning

    Disable job cloning for a given task. Requires secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/disable_job_cloning

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID

    Returns:
        dict: The system response. True for success, False for failure, and an error message.
    """
    tmp_logger = LogWrapper(_logger, f"disable_job_cloning jediTaskID=={jedi_task_id}")
    tmp_logger.debug(f"Start")
    success, message = global_task_buffer.disable_job_cloning(jedi_task_id)
    tmp_logger.debug(f"Done")
    return generate_response(success, message)


@request_validation(_logger, request_method="GET")
def get_status(req, jedi_task_id):
    """
    Get task status

    Get the status of a given task.

    API details:
        HTTP Method: GET
        Path: /task/v1/get_status

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID

    Returns:
        dict: The system response. True for success, False for failure, and an error message.
    """
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    ret = global_task_buffer.getTaskStatus(jedi_task_id)
    if not ret:
        generate_response(False, message="Task not found")
    status = ret[0]
    return generate_response(True, data=status)


@request_validation(_logger, request_method="GET", secure=True)
def get_details(req: PandaRequest, jedi_task_id: int, include_parameters: bool = False, include_status: bool = False):
    """
    Get task details

    Get the details of a given task.

    API details:
        HTTP Method: GET
        Path: /task/v1/get_details

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI Task ID
        include_parameters(bool, optional): flag to include task parameter information (Previously fullFlag)
        include_status(bool, optional): flag to include status information (Previously withTaskInfo)

    Returns:
        dict: The system response. True for success, False for failure, and an error message.
    """

    _logger.debug(f"get_details {jedi_task_id} include_parameters:{include_parameters} include_status:{include_status}")

    details = global_task_buffer.getJediTaskDetails(jedi_task_id, include_parameters, include_status)
    if not details:
        return generate_response(False, message="Task not found or error retrieving the details")

    return generate_response(True, data=details)


@request_validation(_logger, secure=True, production=True, request_method="POST")
def change_attribute(req: PandaRequest, jedi_task_id: int, attribute_name: str, value: int) -> Dict[str, Any]:
    """
    Change a task attribute

    Change a task attribute within the list of valid attributes ("ramCount", "wallTime", "cpuTime", "coreCount"). Requires a secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/change_attribute

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI task ID
        attribute_name(str): attribute to change
        value(int): value to set to the attribute

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """

    # check jedi_task_id
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    # check if attribute_name is valid
    valid_attributes = ["ramCount", "wallTime", "cpuTime", "coreCount"]
    if attribute_name not in valid_attributes:
        return generate_response(False, message=f"{attribute_name} is not a valid attribute. Valid attributes are {valid_attributes}")

    n_tasks_changed = global_task_buffer.changeTaskAttributePanda(jedi_task_id, attribute_name, value)
    if n_tasks_changed is None:  # method excepted
        generate_response(False, message="Exception while changing the attribute")
    if n_tasks_changed == 0:  # no tasks were changed should mean that it doesn't exist
        generate_response(False, message="Task not found")

    return generate_response(True, message=f"{n_tasks_changed} tasks changed")


@request_validation(_logger, secure=True, production=True, request_method="POST")
def change_modification_time(req: PandaRequest, jedi_task_id: int, positive_hour_offset: int) -> Dict[str, Any]:
    """
    Change task modification time

    Change the modification time for a task to `now() + positive_hour_offset`. Requires a secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/change_modification_time

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI task ID
        positive_hour_offset(int): number of hours to add to the current time

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """

    # check jedi_task_id
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    # check offset
    try:
        positive_hour_offset = int(positive_hour_offset)
        new_modification_time = datetime.datetime.now() + datetime.timedelta(hours=positive_hour_offset)
    except ValueError:
        return generate_response(False, message=f"failed to convert {positive_hour_offset} to time")

    n_tasks_changed = global_task_buffer.changeTaskAttributePanda(jedi_task_id, "modificationTime", new_modification_time)
    if n_tasks_changed is None:  # method excepted
        generate_response(False, message="Exception while changing the attribute")
    if n_tasks_changed == 0:  # no tasks were changed should mean that it doesn't exist
        generate_response(False, message="Task not found")

    return generate_response(True, message=f"{n_tasks_changed} tasks changed")


@request_validation(_logger, secure=True, production=True, request_method="POST")
def change_priority(req: PandaRequest, jedi_task_id: int, priority: int):
    """
    Change priority

    Change the priority of a given task. Requires a secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/change_priority

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI task ID
        priority(int): new priority for the task

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    # check jedi_task_id
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    # check priority
    try:
        priority = int(priority)
    except ValueError:
        return generate_response(False, message="priority must be an integer")

    n_tasks_changed = global_task_buffer.changeTaskPriorityPanda(jedi_task_id, priority)

    if n_tasks_changed is None:  # method excepted
        generate_response(False, message="Exception while changing the priority")
    if n_tasks_changed == 0:  # no tasks were changed should mean that it doesn't exist
        generate_response(False, message="Task not found")

    return generate_response(True, message=f"{n_tasks_changed} tasks changed")


@request_validation(_logger, secure=True, production=True, request_method="POST")
def change_split_rule(req: PandaRequest, jedi_task_id: int, attribute_name: str, value: int) -> Dict[str, Any]:
    """
    Change the split rule

    Change the split rule for a task. Requires a secure connection and production role.

    API details:
        HTTP Method: POST
        Path: /task/v1/change_split_rule

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI task ID
        attribute_name(str): split rule attribute to change
        value(int): value to set to the attribute

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """

    # check jedi_task_id
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    # see what the attributes mean in pandaserver/taskbuffer/task_split_rules.py
    valid_attributes = ["AI", "TW", "EC", "ES", "MF", "NG", "NI", "NF", "NJ", "AV", "IL", "LI", "LC", "CC", "OT", "UZ"]
    # check attribute
    if attribute_name not in valid_attributes:
        return generate_response(False, message=f"{attribute_name} is not a valid attribute. Valid attributes are {valid_attributes}", data=2)

    n_tasks_changed = global_task_buffer.changeTaskSplitRulePanda(jedi_task_id, attribute_name, value)
    if n_tasks_changed is None:  # method excepted
        generate_response(False, message="Exception while changing the priority")
    if n_tasks_changed == 0:  # no tasks were changed should mean that it doesn't exist
        generate_response(False, message="Task not found")

    return generate_response(True, message=f"{n_tasks_changed} tasks changed")


@request_validation(_logger, secure=True, request_method="GET")
def get_tasks_modified_since(req, since: str, dn: str = "", full: bool = False, min_task_id: int = None, prod_source_label: str = "user") -> Dict[str, Any]:
    """
    Get tasks modified since

    Get the tasks with `modificationtime > since`. Requires a secure connection.

    API details:
        HTTP Method: GET
        Path: /task/v1/get_tasks_modified_since

    Args:
        req(PandaRequest): internally generated request object
        since(str): time in the format `%Y-%m-%d %H:%M:%S`, e.g. `2024-12-18 14:30:45`. The tasks with `modificationtime > since` will be returned
        dn(str, optional): user DN
        full(bool, optional): flag to include full task information. If `full=False` the basic fields are `jediTaskID, modificationTime, status, processingType, transUses, transHome, architecture, reqID, creationDate, site, cloud, taskName`
        min_task_id(int, optional): minimum task ID
        prod_source_label(str, optional): task type (e.g. `user`, `managed`, `test`, etc.)

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """

    if not dn:
        dn = get_dn(req)

    try:
        min_task_id = int(min_task_id)
    except ValueError:
        min_task_id = None

    _logger.debug(f"get_tasks_modified_since dn:{dn} since:{since} full:{full} min_task_id:{min_task_id} prod_source_label:{prod_source_label}")

    tasks = global_task_buffer.getJediTasksInTimeRange(dn, since, full, min_task_id, prod_source_label)
    return generate_response(True, data=tasks)


@request_validation(_logger, secure=True, request_method="GET")
def get_datasets_and_files(req, jedi_task_id, dataset_types: List = ("input", "pseudo_input")) -> Dict[str, Any]:
    """
    Get datasets and files

    Get the files in the datasets associated to a given task. You can filter passing a list of dataset types. The return format is:
    ```
    [
        {
            "dataset": {
                "name": dataset_name,
                "id": dataset_id
            },
            "files": [
                {
                    "lfn": lfn,
                    "scope": file_scope,
                    "id": file_id,
                    "status": status
                },
                ...
            ]
        },
        ...
    ]
    ```
    Requires a secure connection.

    API details:
        HTTP Method: GET
        Path: /task/v1/get_datasets_and_files

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI task ID
        dataset_types(List, optional): list of dataset types, defaults to `["input", "pseudo_input"]`

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """

    data = global_task_buffer.get_data_in_datasets(jedi_task_id, dataset_types)
    if data is None:
        return generate_response(False, message="Database exception while gathering files")
    if data == []:
        return generate_response(False, message="No data found for the task")

    return generate_response(True, data=data)


@request_validation(_logger, secure=True, request_method="GET")
def get_job_ids(req: PandaRequest, jedi_task_id: int) -> Dict[str, Any]:
    """
    Get job IDs

    Get a list with the job IDs `[job_id, ...]` (in any status) associated to a given task. Requires a secure connection.

    API details:
        HTTP Method: GET
        Path: /task/v1/get_job_ids

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI task ID

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    try:
        jedi_task_id = int(jedi_task_id)
    except ValueError:
        return generate_response(False, message=MESSAGE_TASK_ID)

    job_id_list = global_task_buffer.getPandaIDsWithTaskID(jedi_task_id)
    return generate_response(True, data=job_id_list)


@request_validation(_logger, secure=True, request_method="POST")
def insert_task_parameters(req: PandaRequest, task_parameters: Dict, parent_tid: int = None) -> Dict[str, Any]:
    """
    Register task

    Insert the task parameters to register a task. Requires a secure connection.

    API details:
        HTTP Method: POST
        Path: /task/v1/insert_task_parameters

    Args:
        req(PandaRequest): internally generated request object
        task_parameters(dict): Dictionary with all the required task parameters.
        parent_tid(int, optional): Parent task ID

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """
    tmp_log = LogWrapper(_logger, f"insertTaskParams-{datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat('/')}")
    tmp_log.debug("start")

    user = get_dn(req)
    is_production_role = has_production_role(req)
    fqans = get_fqan(req)

    tmp_log.debug(f"user={user} is_production_role={is_production_role} FQAN:{str(fqans)} parent_tid={parent_tid}")
    ret = global_task_buffer.insertTaskParamsPanda(task_parameters, user, is_production_role, fqans, properErrorCode=True, parent_tid=parent_tid, decode=False)

    code, message = ret
    success = code == 0
    if not success:
        return generate_response(False, message=message, data=code)

    # Extract the task ID from the message
    jedi_task_id = None
    match = re.search(r"jediTaskID=(\d+)", message)
    if match:
        try:
            jedi_task_id = int(match.group(1))  # Convert to an integer
        except ValueError:
            jedi_task_id = None

    return generate_response(True, message=message, data=jedi_task_id)


@request_validation(_logger, request_method="GET")
def get_task_parameters(req: PandaRequest, jedi_task_id: int) -> Dict[str, Any]:
    """
    Get task parameters

    Get a dictionary with the task parameters used to create a task.

    API details:
        HTTP Method: GET
        Path: /task/v1/get_task_parameters

    Args:
        req(PandaRequest): internally generated request object
        jedi_task_id(int): JEDI task ID

    Returns:
        dict: The system response. True for success, False for failure, and an error message. Return code in the data field, 0 for success, others for failure.
    """

    # validate the task id
    try:
        jedi_task_id = int(jedi_task_id)
    except Exception:
        return generate_response(False, message=MESSAGE_TASK_ID)

    # get the parameters
    task_parameters_str = global_task_buffer.getTaskParamsMap(jedi_task_id)
    if not task_parameters_str:
        return generate_response(False, message="Task not found")

    # decode the parameters
    try:
        task_parameters = json.loads(task_parameters_str)
    except json.JSONDecodeError as e:
        return generate_response(False, message=f"Error decoding the task parameters: {str(e)}")

    return generate_response(True, data=task_parameters)
