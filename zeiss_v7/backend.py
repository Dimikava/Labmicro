"""Lifecycle wrapper for the existing Pycro-Manager Java-headless backend."""


class JavaHeadlessBackend:
    """Start and stop Micro-Manager without changing the proven call contract."""

    def __init__(self, mm_app_path, config_file, debug=False):
        self.mm_app_path = mm_app_path
        self.config_file = config_file
        self.debug = debug
        self._stop_headless = None
        self.started = False

    def start(self):
        from pycromanager import Core, start_headless, stop_headless

        start_headless(
            mm_app_path=self.mm_app_path,
            config_file=self.config_file,
            python_backend=False,
            debug=self.debug,
        )
        self._stop_headless = stop_headless
        self.started = True
        return Core()

    def stop(self):
        if not self.started:
            return
        self._stop_headless()
        self.started = False
