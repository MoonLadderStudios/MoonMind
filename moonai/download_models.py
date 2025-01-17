import os
import sys

from download_model import download_model


def download_models(model_names=None):
    hf_access_token = os.getenv("HF_ACCESS_TOKEN")
    model_directory = os.getenv("MODEL_DIRECTORY")
    if model_names is None:
        model_names = os.getenv("MODEL_NAMES").split(",")

    all_models_downloaded = True
    for model_name in model_names:
        if not download_model(hf_access_token, model_directory, model_name):
            all_models_downloaded = False
    return all_models_downloaded

if __name__ == "__main__":
    model_names = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    download_models(model_names)
