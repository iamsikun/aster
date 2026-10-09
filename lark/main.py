from dotenv import load_dotenv
from .logging_config import configure_logging
from .config import load_config
from .pipelines.daily import run_daily

def main():
    load_dotenv()
    configure_logging()
    cfg = load_config()
    run_daily(cfg)

if __name__ == "__main__":
    main()
