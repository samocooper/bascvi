from argparse import ArgumentParser
import os
from typing import Dict
import logging
import json
import pandas as pd
import pickle

from pytorch_lightning import Trainer
import torch

from bascvi.datamodule import TileDBSomaIterDataModule, AnnDataDataModule, EmbDatamodule

from bascvi.datamodule.soma.soma_helpers import open_soma_experiment

logger = logging.getLogger("pytorch_lightning")

def predict(config: Dict):

    logger.info("--------------Loading model from checkpoint-------------")

    if "pretrained_model_path" not in config:
        raise ValueError("Config must have a 'pretrained_model_path' key in predict mode")

    # dynamically import trainer class
    module = __import__("bascvi.trainer", globals(), locals(), [config["trainer_module_name"] if "trainer_module_name" in config else "bascvi_trainer"], 0)
    EmbeddingTrainer = getattr(module, config["trainer_class_name"] if "trainer_class_name" in config else "BAScVITrainer")

    # Load the model from checkpoint
    checkpoint = torch.load(config["pretrained_model_path"])
    if "gene_list" not in checkpoint['hyper_parameters']:
        with open("/home/ubuntu/paper_repo/bascvi/checkpoints/paper/gene_list_30.txt", 'r') as f:
            pretrained_gene_list = f.read().split(",\n")
            # strip the quotes
            pretrained_gene_list = [gene.strip('"') for gene in pretrained_gene_list]
            print(pretrained_gene_list[:5])
            
        #raise ValueError("Pretrained model must have a 'gene_list' key in hyper_parameters")
    else:
        pretrained_gene_list = checkpoint['hyper_parameters']['gene_list']
    n_input = checkpoint['state_dict']['vae.px_r'].shape[0]
    assert n_input == len(pretrained_gene_list), f"Number of genes in the model {n_input} does not match the gene list length {len(pretrained_gene_list)}"
    n_batch = checkpoint['state_dict']['vae.z_encoder.encoder.Layer_0.0.weight'].shape[1] - n_input
    model = EmbeddingTrainer.load_from_checkpoint(config["pretrained_model_path"], root_dir=config["run_save_dir"], n_input=n_input, n_batch=n_batch)

    logger.info("--------------Setting up data module--------------")
    # Set the gene list in the datamodule from the saved model
    config["datamodule"]["options"]["pretrained_gene_list"] = pretrained_gene_list
    # Set the number of batches in the datamodule from the saved model
    config["datamodule"]["options"]["pretrained_batch_size"] = model.model_args.get("n_batch", None)

    if "class_name" not in config["datamodule"]:
        config["datamodule"]["class_name"] = "TileDBSomaIterDataModule"

    if config["datamodule"]["class_name"] == "TileDBSomaIterDataModule":
        config["datamodule"]["options"]["root_dir"] = config["run_save_dir"]
        datamodule = TileDBSomaIterDataModule(**config["datamodule"]["options"])

    elif config["datamodule"]["class_name"] == "EmbDatamodule":
        datamodule = EmbDatamodule(**config["datamodule"]["options"])

    elif config["datamodule"]["class_name"] == "AnnDataDataModule":
        datamodule = AnnDataDataModule(**config["datamodule"]["options"])

    datamodule.setup(stage="predict")

    model.datamodule = datamodule

    # Create a Trainer instance with minimal configuration, since we're only predicting
    trainer = Trainer(default_root_dir=config["run_save_dir"], accelerator="auto")

    logger.info("--------------Embedding prediction on full dataset-------------")
    predictions = trainer.predict(model, datamodule=datamodule)
    embeddings = torch.cat(predictions, dim=0).detach().cpu().numpy()
    emb_columns = ["embedding_" + str(i) for i in range(embeddings.shape[1] - 2 )] # -2 accounts for soma_joinid and cell_idx
    embeddings_df = pd.DataFrame(data=embeddings, columns=emb_columns + ["soma_joinid"] + ["cell_idx"])

    # logger.info("--------------------------Run UMAP----------------------------")
    # if embeddings_df.shape[0] > 500000:
    #     logger.info("Too many embeddings to calculate UMAP, skipping....")
    # else:

    #     embeddings_df = umap_calc_and_save_html(embeddings_df, emb_columns, trainer.default_root_dir)
    
    logger.info("-----------------------Save Embeddings------------------------")
    if config["datamodule"]["class_name"] in ["TileDBSomaIterDataModule", "EmbDatamodule"]:

        with open_soma_experiment(datamodule.soma_experiment_uri) as soma_experiment:
            obs_df = soma_experiment.obs.read(
                                column_names=("soma_joinid", "barcode", "standard_true_celltype", "authors_celltype", "cell_type_pred", "cell_subtype_pred", "sample_name", "study_name", "batch_name"),
                            ).concat().to_pandas()
                        
            # merge the embeddings with the soma join id
            embeddings_df = embeddings_df.set_index("soma_joinid").join(obs_df.set_index("soma_joinid"))

    save_path = os.path.join(config["run_save_dir"], "pred_embeddings_" + os.path.splitext(os.path.basename(config["pretrained_model_path"]))[0] + ".tsv")
    embeddings_df.to_csv(save_path, sep="\t")
    logger.info(f"Saved predicted embeddings to: {save_path}")


if __name__ == "__main__":

    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "-c",
        "--config",
        help="Config file path, `./configs/train_scvi_cfg.json`",
        type=str,
        default="./configs/train_scvi_cfg.json",
    )
    parser.add_argument(
        "-l",
        "--checkpoint_path",
        help="Checkpoint file path, `./exp_logs/v6/baseline_no_disc/lightning_logs/version_7/checkpoints/scvi-vae-epoch=11-elbo_val=0.00.ckpt`",
        type=str,
        default="",
    )
    args = parser.parse_args()

    logger.info(f"Reading config file from location: {args.config}")
    with open(args.config) as json_file:
        cfg = json.load(json_file)


    predict(cfg, args.checkpoint_path)
