"""Configure local paths before importing browser/SDK dependencies."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure():
    for name, value in {
        'CRAWL4_AI_BASE_DIRECTORY': str(ROOT / '.cache'),
        'CRAWL4AI_NO_CLOUD_NOTICE': '1',
        'HF_HOME': str(ROOT / '.cache' / 'huggingface'),
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'LITELLM_LOCAL_MODEL_COST_MAP': 'True', 'LITELLM_TELEMETRY': 'False',
        'PYTHON_DOTENV_DISABLED': '1', 'XDG_CACHE_HOME': str(ROOT / '.cache'),
    }.items():
        os.environ[name] = value
    (ROOT / '.tmp').mkdir(exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(ROOT / '.tmp')
    os.environ['CRAWL4AI_CHROMIUM_SANDBOX'] = 'true' if os.name == 'nt' else 'false'
    if os.environ.get('APIFY_IS_AT_HOME') != '1':
        os.environ['APIFY_LOCAL_STORAGE_DIR'] = str(ROOT / 'storage')
        os.environ['CRAWLEE_STORAGE_DIR'] = str(ROOT / 'storage')
        os.environ['APIFY_PURGE_ON_START'] = 'false'
        os.environ['CRAWLEE_PURGE_ON_START'] = 'false'
        os.environ['ACTOR_INPUT_KEY'] = 'INPUT_BACKMARKET'


configure()
