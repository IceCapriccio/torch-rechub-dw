import sys

sys.path.append("../..")

import os
import numpy as np
import pandas as pd
import torch

from sklearn.preprocessing import MinMaxScaler, LabelEncoder
from torch_rechub.models.matching import GRU4Rec
from torch_rechub.trainers import MatchTrainer
from torch_rechub.basic.features import DenseFeature, SparseFeature, SequenceFeature
from torch_rechub.utils.match import generate_seq_feature_match, gen_model_input
from torch_rechub.utils.data import df_to_dict, MatchDataGenerator
from movielens_utils import match_evaluation


def get_movielens_data(data_path, load_cache=False):
    data = pd.read_csv(data_path)
    data["cate_id"] = data["genres"].apply(lambda x: x.split("|")[0])
    sparse_features = ['user_id', 'movie_id', 'gender', 'age', 'occupation', 'zip', "cate_id"]
    user_col, item_col, label_col = "user_id", "movie_id", "label"

    feature_max_idx = {}
    for feature in sparse_features:
        lbe = LabelEncoder()
        data[feature] = lbe.fit_transform(data[feature]) + 1
        feature_max_idx[feature] = data[feature].max() + 1
        if feature == user_col:
            user_map = {encode_id + 1: raw_id for encode_id, raw_id in enumerate(lbe.classes_)}  #encode user id: raw user id
        if feature == item_col:
            item_map = {encode_id + 1: raw_id for encode_id, raw_id in enumerate(lbe.classes_)}  #encode item id: raw item id
    np.save("./data/ml-1m/saved/raw_id_maps.npy", (user_map, item_map))

    user_profile = data[["user_id", "gender", "age", "occupation", "zip"]].drop_duplicates('user_id')
    item_profile = data[["movie_id", "cate_id"]].drop_duplicates('movie_id')

    if load_cache:  #if you have run this script before and saved the preprocessed data
        x_train, y_train, x_test = np.load("./data/ml-1m/saved/data_cache.npy", allow_pickle=True)
    else:
        #Note: mode=2 means list-wise negative sample generate, saved in last col "neg_items"
        df_train, df_test = generate_seq_feature_match(data,
                                                       user_col,
                                                       item_col,
                                                       time_col="timestamp",
                                                       item_attribute_cols=[],
                                                       sample_method=1,
                                                       mode=2,
                                                       neg_ratio=3,
                                                       min_item=0)
        x_train = gen_model_input(df_train, user_profile, user_col, item_profile, item_col, seq_max_len=50, padding='post', truncating='post')
        y_train = np.array([0] * df_train.shape[0])  #label=0 means the first pred value is positiva sample
        x_test = gen_model_input(df_test, user_profile, user_col, item_profile, item_col, seq_max_len=50, padding='post', truncating='post')
        np.save("./data/ml-1m/saved/data_cache.npy", (x_train, y_train, x_test))

    user_cols = ['user_id', 'gender', 'age', 'occupation', 'zip']

    user_features = [SparseFeature(name, vocab_size=feature_max_idx[name], embed_dim=16) for name in user_cols]
    history_features = [
        SequenceFeature("hist_movie_id",
                        vocab_size=feature_max_idx["movie_id"],
                        embed_dim=16,
                        pooling="concat",
                        shared_with="movie_id")
    ]

    item_features = [SparseFeature('movie_id', vocab_size=feature_max_idx['movie_id'], embed_dim=16)]
    neg_item_feature = [
        SequenceFeature('neg_items',
                        vocab_size=feature_max_idx['movie_id'],
                        embed_dim=16,
                        pooling="concat",
                        shared_with="movie_id")
    ]

    all_item = df_to_dict(item_profile)
    test_user = x_test
    return user_features, history_features, item_features, neg_item_feature, x_train, y_train, all_item, test_user


def main(dataset_path, model_name, epoch, learning_rate, batch_size, weight_decay, device, save_dir, seed):
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)
    torch.manual_seed(seed)
    user_features, history_features, item_features, neg_item_feature, x_train, y_train, all_item, test_user = get_movielens_data(dataset_path)
    dg = MatchDataGenerator(x=x_train, y=y_train)

    model = GRU4Rec(user_features, history_features, item_features, neg_item_feature, user_params={"dims": [128, 64, 16]}, temperature=0.02)

    #mode=1 means pair-wise learning
    trainer = MatchTrainer(model,
                           mode=2,
                           optimizer_params={
                               "lr": learning_rate,
                               "weight_decay": weight_decay
                           },
                           n_epoch=epoch,
                           device=device,
                           model_path=save_dir,
                           gpus=[0])

    train_dl, test_dl, item_dl = dg.generate_dataloader(test_user, all_item, batch_size=batch_size, num_workers=0)
    trainer.fit(train_dl)

    print("inference embedding")
    user_embedding = trainer.inference_embedding(model=model, mode="user", data_loader=test_dl, model_path=save_dir)
    item_embedding = trainer.inference_embedding(model=model, mode="item", data_loader=item_dl, model_path=save_dir)
    print(user_embedding.shape, item_embedding.shape)
    #torch.save(user_embedding.data.cpu(), save_dir + "user_embedding.pth")
    #torch.save(item_embedding.data.cpu(), save_dir + "item_embedding.pth")
    match_evaluation(user_embedding, item_embedding, test_user, all_item, topk=10)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset_path', default="./data/ml-1m/ml-1m_sample.csv")
    parser.add_argument('--model_name', default='gru4rec')
    parser.add_argument('--epoch', type=int, default=10)  #10
    parser.add_argument('--learning_rate', type=float, default=1e-3)
    parser.add_argument('--batch_size', type=int, default=256)  #4096
    parser.add_argument('--weight_decay', type=float, default=1e-6)
    parser.add_argument('--device', default='cuda:0')  #cuda:0
    parser.add_argument('--save_dir', default='./data/ml-1m/saved/')
    parser.add_argument('--seed', type=int, default=2022)

    args = parser.parse_args()
    main(args.dataset_path, args.model_name, args.epoch, args.learning_rate, args.batch_size, args.weight_decay, args.device,
         args.save_dir, args.seed)
"""
python run_ml_youtube_dnn.py
"""