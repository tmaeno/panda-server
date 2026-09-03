import datetime
import time
import traceback
import uuid
from typing import Any

from pandacommon.pandalogger.LogWrapper import LogWrapper
from pandacommon.pandalogger.PandaLogger import PandaLogger
from pandacommon.pandautils.PandaUtils import naive_utcnow

from pandaserver.config import panda_config
from pandaserver.dataservice import DataServiceUtils

_log = PandaLogger().getLogger("broker")


def schedule(jobs, siteMapper):
    timestamp = naive_utcnow().isoformat("/")
    tmp_log = LogWrapper(_log, f"start_ts={timestamp}")

    try:
        # no jobs
        if len(jobs) == 0:
            tmp_log.debug("finished : no jobs")
            return

        max_jobs = 20
        max_files = 20

        iJob = 0
        fileList: list[Any] = []
        # the SiteSpec chosen for the current bunch, and None while there is no site to
        # choose one from
        chosen_panda_queue: Any = None
        prodDBlock = None
        computingSite = None
        dispatchDBlock = None
        previousCloud = None
        prevProType = None
        prevSourceLabel = None
        prevDirectAcc = None
        prevIsJEDI = None
        prevHasPresetSite = None

        indexJob = 0

        # loop over all jobs + terminator(None)
        for job in jobs + [None]:
            indexJob += 1

            # ignore failed jobs
            if job and job.jobStatus == "failed":
                continue

            # whether the site was picked for the job before it got here. Bunches are cut when
            # this changes, which is what comparing the old special-brokerage site lists did
            hasPresetSite = bool(job and job.computingSite != "NULL" and job.prodSourceLabel in ("test", "managed"))

            overwriteSite = False

            # check JEDI
            isJEDI = False
            if job and job.lockedby == "jedi":
                isJEDI = True

            # new bunch or terminator
            if (
                job is None
                or len(fileList) >= max_files
                or (dispatchDBlock is None and job.homepackage.startswith("AnalysisTransforms"))
                or prodDBlock != job.prodDBlock
                or job.computingSite != computingSite
                or iJob > max_jobs
                or previousCloud != job.getCloud()
                or prevDirectAcc != job.transferType
                or prevProType != job.processingType
                or prevHasPresetSite != hasPresetSite
                or prevIsJEDI != isJEDI
            ):
                if indexJob > 1:
                    tmp_log.debug("new bunch")
                    tmp_log.debug(f"  iJob           {iJob}")
                    tmp_log.debug(f"  cloud          {previousCloud}")
                    tmp_log.debug(f"  sourceLabel    {prevSourceLabel}")
                    tmp_log.debug(f"  prodDBlock     {prodDBlock}")
                    tmp_log.debug(f"  computingSite  {computingSite}")
                    tmp_log.debug(f"  processingType {prevProType}")
                    tmp_log.debug(f"  transferType   {prevDirectAcc}")

                # terminate
                if job is None:
                    break
                # reset iJob
                iJob = 0
                # reset file list
                fileList = []
                # create new dispDBlock
                if job.prodDBlock != "NULL":
                    # get datatype
                    try:
                        tmpDataType = job.prodDBlock.split(":")[-1].split(".")[-2]
                    except Exception:
                        # default
                        tmpDataType = "GEN"
                    if len(tmpDataType) > 20:
                        # avoid too long name
                        tmpDataType = "GEN"
                    transferType = "transfer"
                    if job.useInputPrestaging():
                        transferType = "prestaging"
                    dispatchDBlock = f"panda.{job.taskID}.{time.strftime('%m.%d')}.{tmpDataType}.{transferType}.{str(uuid.uuid4())}_dis{job.PandaID}"
                    tmp_log.debug(f"New dispatchDBlock: {dispatchDBlock}")
                prodDBlock = job.prodDBlock
                # already define computingSite
                if job.computingSite != "NULL":
                    # instantiate KnownSite
                    chosen_panda_queue = siteMapper.getSite(job.computingSite)

                    # if site doesn't exist, use the default site
                    if job.homepackage.startswith("AnalysisTransforms"):
                        if chosen_panda_queue.sitename == panda_config.def_sitename:
                            chosen_panda_queue = siteMapper.getSite(panda_config.def_queue)
                            overwriteSite = True
                else:
                    # default for Analysis jobs
                    if job.homepackage.startswith("AnalysisTransforms"):
                        chosen_panda_queue = siteMapper.getSite(panda_config.def_queue)
                        overwriteSite = True
                    else:
                        # nothing picks a site here any more, so the job keeps not having one
                        chosen_panda_queue = None
            # increment iJob
            iJob += 1
            # reserve computingSite and cloud
            computingSite = job.computingSite
            previousCloud = job.getCloud()
            prevProType = job.processingType
            prevSourceLabel = job.prodSourceLabel
            prevDirectAcc = job.transferType
            prevHasPresetSite = hasPresetSite
            prevIsJEDI = isJEDI

            # assign site
            if chosen_panda_queue is not None:
                job.computingSite = chosen_panda_queue.sitename
                tmp_log.debug(f"PandaID:{job.PandaID} -> preset site:{chosen_panda_queue.sitename}")
                # set cloud
                if job.cloud in ["NULL", None, ""]:
                    job.cloud = chosen_panda_queue.cloud

            # set destinationSE
            destSE = job.destinationSE
            if siteMapper.checkCloud(job.getCloud()):
                # use cloud dest for non-existing sites
                if job.prodSourceLabel != "user" and job.destinationSE not in siteMapper.siteSpecList and job.destinationSE != "local":
                    if DataServiceUtils.checkJobDestinationSE(job):
                        destSE = DataServiceUtils.checkJobDestinationSE(job)
                    job.destinationSE = destSE

            if overwriteSite:
                # overwrite SE for analysis jobs which set non-existing sites
                destSE = job.computingSite
                job.destinationSE = destSE

            # set dispatchDBlock and destinationSE
            first = True
            for file in job.Files:
                # Set dispatch data block for pre-stating jobs too
                if file.type == "input" and file.dispatchDBlock == "NULL" and file.status not in ["ready", "missing", "cached"]:
                    if first:
                        first = False
                        job.dispatchDBlock = dispatchDBlock
                    file.dispatchDBlock = dispatchDBlock
                    file.status = "pending"
                    if file.lfn not in fileList:
                        fileList.append(file.lfn)

                # destinationSE
                if file.type in ["output", "log"] and destSE != "":
                    if job.prodSourceLabel == "user" and job.computingSite == file.destinationSE:
                        pass
                    elif job.prodSourceLabel == "user" and prevIsJEDI is True and file.destinationSE not in ["", "NULL"]:
                        pass
                    elif destSE == "local":
                        pass
                    elif DataServiceUtils.getDistributedDestination(file.destinationDBlockToken):
                        pass
                    else:
                        file.destinationSE = destSE

                # pre-assign GUID to log
                if file.type == "log":
                    # generate GUID
                    file.GUID = str(uuid.uuid4())

        tmp_log.debug("finished")

    except Exception as e:
        tmp_log.error(f"schedule : {str(e)} {traceback.format_exc()}")
