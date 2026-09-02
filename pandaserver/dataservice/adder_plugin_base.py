"""
The Adder module is the core for add_main to post-process jobs’ output data, such as data registration, trigger data aggregation and so on.
Those post-processing procedures are experiment-dependent so that the Adder also has a plugin structure to load an experiment-specific plugin.

"""

from typing import TYPE_CHECKING

from .adder_result import AdderResult

if TYPE_CHECKING:
    from pandacommon.pandalogger.LogWrapper import LogWrapper


class AdderPluginBase:
    """
    Base class for Adder plugins.
    """

    # Every caller passes logger= in params, and the loop below installs it, so the
    # placeholder assigned in __init__ never survives construction. Declared
    # non-Optional because a plugin method running without a logger is a bug either
    # way, and an Optional type would only push a None check onto every log call.
    logger: "LogWrapper"

    def __init__(self, job, params):
        """
        Initialize the AdderPluginBase.

        :param job: The job object.
        :param params: Additional parameters.
        """
        self.job = job
        self.logger = None  # type: ignore[assignment]
        self.result = AdderResult()
        self.extra_info = {}
        for key, value in params.items():
            setattr(self, key, value)
