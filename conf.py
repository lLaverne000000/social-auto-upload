from sau_runtime import get_runtime_paths

_RUNTIME_PATHS = get_runtime_paths()
BASE_DIR = _RUNTIME_PATHS.data_root
RESOURCE_DIR = _RUNTIME_PATHS.resource_root

XHS_SERVER = "http://127.0.0.1:11901"
LOCAL_CHROME_PATH = ""
LOCAL_CHROME_HEADLESS = True
DEBUG_MODE = True
YT_PROXY = None
