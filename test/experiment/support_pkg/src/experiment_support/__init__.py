from boltons.strutils import slugify


def get_message() -> str:
    slugify("host local dependency")
    return "experiment-host-v1"
