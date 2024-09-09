from argparse import ArgumentParser
import os
from typing import Dict
import logging
import json

from pytorch_lightning import Trainer
import torch

from datamodule import ScRNASeqDataModule
from utils.utils import umap_calc_and_save_html

logger = logging.getLogger("pytorch_lightning")
logger.addHandler(logging.FileHandler("std.log"))

def train(cfg: Dict):
    scrna_datamodule = ScRNASeqDataModule(**cfg["datamodule"])
    logger.info("Set up data module....")
    scrna_datamodule.setup()
    logger.info(f"{scrna_datamodule.full_train_adata}")

    # dynamically import trainer class
    module = __import__("trainer", globals(), locals(), [cfg["trainer_module_name"]], 0)
    EmdeddingTrainer = getattr(module, cfg["trainer_class_name"])

    logger.info(f"Initializing Custom Embedding Trainer.....")
    model = EmdeddingTrainer.load_from_checkpoint("XX")
    
    print(model)
    # add callbacks to pytroch lightning trainer config
    cfg["pl_trainer"]["callbacks"] = model.callbacks

    logger.info(f"Initializing pytorch-lightning trainer.....")
    trainer = Trainer(**cfg["pl_trainer"])  # `Trainer(accelerator='gpu', devices=1)`
            
    trainer.fit(model, datamodule=scrna_datamodule) # Hit ctrl C to trigger predict as soon as training starts - Hack but avoids a lot of extra code
    

    print("reloading model")    
    model = EmdeddingTrainer.load_from_checkpoint("XX")


    predictions = trainer.predict(model, datamodule=scrna_datamodule)
    embeddings = torch.cat(predictions, dim=0).detach().cpu().numpy()

    emb_columns = ["embedding_" + str(i) for i in range(embeddings.shape[1])]
    scrna_datamodule.full_train_adata.obs[emb_columns] = embeddings

    logger.info("--------------------------Run UMAP----------------------------")
    embeddings_df = umap_calc_and_save_html(scrna_datamodule.full_train_adata.obs, emb_columns, trainer.default_root_dir)
    embeddings_df.to_csv(os.path.join(trainer.default_root_dir, "train_embeddings.tsv"), sep="\t")
    logger.info(f"Saved predicted embeddings in embeddings.tsv located at {trainer.default_root_dir}/train_embeddings.tsv")


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "-c",
        "--config",
        help="Config file path, `./configs/train_scvi_cfg.json`",
        type=str,
        default="./configs/train_scvi_cfg.json",
    )
    args = parser.parse_args()

    logger.info(f"Reading config file from location: {args.config}")
    with open(args.config) as json_file:
        cfg = json.load(json_file)

    
    train(cfg)
