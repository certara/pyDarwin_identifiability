import os

from abc import ABC, abstractmethod

from darwin.Log import log
from darwin.options import options

_model_run_man = None
_model_run_man_classes = {}


class ModelRunManager(ABC):

    @staticmethod
    def init_folders():
        log.message('Preparing project working folder...')

        if not os.path.exists(options.working_dir):
            os.makedirs(options.working_dir)

        log.message('Preparing project output folder...')

        if not os.path.exists(options.output_dir):
            os.makedirs(options.output_dir)

    @staticmethod
    def cleanup_folders():
        pass

    @abstractmethod
    def _preprocess_runs(self, runs: list) -> list:
        pass

    @abstractmethod
    def _process_runs(self, runs: list) -> list:
        pass

    @abstractmethod
    def _postprocess_runs(self, runs: list) -> list:
        pass

    def run_all(self, runs: list):
        """
        Runs the models. Always runs from integer representation. For GA will need to convert to integer.
        For downhill, will need to convert to minimal binary, then to integer.
        """

        runs = self._preprocess_runs(runs)

        runs = self._process_runs(runs)

        runs = self._postprocess_runs(runs)

        return runs


def set_run_manager(man):
    global _model_run_man

    _model_run_man = man


def get_run_manager():
    return _model_run_man


def register_model_run_man(man_name, mrm_class):
    _model_run_man_classes[man_name] = mrm_class


def create_model_run_man(man_name) -> ModelRunManager:
    return _model_run_man_classes[man_name]()
