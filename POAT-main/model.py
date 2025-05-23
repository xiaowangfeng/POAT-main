import torch
import torch.nn as nn
import json
import numpy as np
import pandas as pd
import math
import torch.nn.functional as F

torch.manual_seed(0)


def weighted_softmax(x, dim=-1, weights=None):
    maxes = torch.max(x, dim, keepdim=True)[0]
    x_exp = torch.exp(x-maxes)
    if weights is not None:
        x_exp = weights * x_exp
    x_exp_sum = torch.sum(x_exp, dim, keepdim=True)
    probs = x_exp/x_exp_sum
    return probs


def expand_mask(mask):
    assert mask.ndim > 2, "Mask must be at least 2-dimensional with seq_length x seq_length"
    if mask.ndim == 3:
        mask = mask.unsqueeze(1)
    while mask.ndim < 4:
        mask = mask.unsqueeze(0)
    return mask


# class MHA(nn.Module):
#
#     def __init__(self, input_dim, embed_dim, num_heads, dropout=0.0, use_kv_bias=False):
#         super().__init__()
#         assert embed_dim % num_heads == 0, "Embedding dimension must be 0 modulo number of heads."
#
#         self.use_kv_bias = use_kv_bias
#         self.embed_dim = embed_dim
#         self.num_heads = num_heads
#         self.head_dim = embed_dim // num_heads
#         self.dropout = nn.Dropout(p=dropout)
#         self.qkv_proj = nn.Linear(input_dim, 3 * embed_dim)
#         self.o_proj = nn.Linear(embed_dim, embed_dim)
#         self.delta_mul = nn.Linear(embed_dim, embed_dim)
#         self.delta_bias = nn.Linear(embed_dim, embed_dim)
#         self._reset_parameters()
#
#     def _reset_parameters(self):
#         #  From original torch implementation
#         nn.init.xavier_uniform_(self.qkv_proj.weight)
#         self.qkv_proj.bias.data.fill_(0)
#         nn.init.xavier_uniform_(self.o_proj.weight)
#         self.o_proj.bias.data.fill_(0)

###
class Point_Transformer_Last(nn.Module):
    def __init__(self,embedding_dim):
        super(Point_Transformer_Last, self).__init__()
        self.embedding_dim = embedding_dim
        self.sa1 = SA_Layer(embedding_dim)
        self.sa2 = SA_Layer(embedding_dim)
        self.sa3 = SA_Layer(embedding_dim)
        self.sa4 = SA_Layer(embedding_dim)

    def forward(self, x):
            # B, D, N
        x1 = self.sa1(x)
        x2 = self.sa2(x1)
        x = torch.cat((x1, x2), dim=1)
        return x
class SA_Layer(nn.Module):
    def __init__(self, embedding_dim):
        super(SA_Layer, self).__init__()
        self.q_conv = nn.Conv1d(embedding_dim, embedding_dim // 2, 1, bias=False)
        self.k_conv = nn.Conv1d(embedding_dim, embedding_dim // 2, 1, bias=False)
        # self.q_conv.conv.weight = self.k_conv.conv.weight
        self.v_conv = nn.Conv1d(embedding_dim, embedding_dim, 1)
        self.trans_conv = nn.Conv1d(embedding_dim, embedding_dim, 1)
        self.after_norm = nn.BatchNorm1d(embedding_dim)
        self.act = nn.ReLU()
        self.softmax = nn.Softmax(dim=-1)

    def forward(self, x):
        x_q = self.q_conv(x).permute(0, 2, 1)  # b, n, c
        x_k = self.k_conv(x)  # b, c, n
        x_v = self.v_conv(x)
        energy = torch.bmm(x_q, x_k)  # b, n, n
        attention = self.softmax(energy)
        attention = attention / (1e-9 + attention.sum(dim=1, keepdims=True))
        x_r = torch.bmm(x_v, attention)  # b, c, n
        x_r = self.act(self.after_norm(self.trans_conv(x - x_r)))
        x = x + x_r
        return x

###自定义函数结束


class VectorAttention(nn.Module):
    def __init__(
        self,
        embed_channels,
        attention_dropout=0.0,
        qkv_bias=True,
        activation=nn.ReLU
    ):
        super(VectorAttention, self).__init__()
        self.embed_channels = embed_channels
        self.attn_drop_rate = attention_dropout
        self.qkv_bias = qkv_bias

        self.linear_q = nn.Sequential(
            nn.Linear(embed_channels, embed_channels, bias=qkv_bias),
            nn.LayerNorm(embed_channels),
            activation(inplace=True),
        )
        self.linear_k = nn.Sequential(
            nn.Linear(embed_channels, embed_channels, bias=qkv_bias),
            nn.LayerNorm(embed_channels),
            activation(inplace=True),
        )

        self.linear_v = nn.Linear(embed_channels, embed_channels, bias=qkv_bias)

        self.weight_encoding = nn.Sequential(
            nn.Linear(embed_channels, embed_channels),
            nn.LayerNorm(embed_channels),
            activation(inplace=True),
            nn.Linear(embed_channels, embed_channels),
        )
        self.softmax = nn.Softmax(dim=1)
        self.attn_drop = nn.Dropout(attention_dropout)

    def forward(self, feat, distribution):
        query, key, value = (
            self.linear_q(feat),
            self.linear_k(feat),
            self.linear_v(feat),
        )
        relation_qk = key.unsqueeze(-3) - query.unsqueeze(-2)
        weight = self.weight_encoding(relation_qk)
        weight = self.attn_drop(weighted_softmax(weight, dim=-2, weights=distribution.unsqueeze(1)))

        mask = (distribution * distribution.transpose(-1, -2)) > 0
        weight = weight * mask.unsqueeze(-1)
        feat = torch.einsum("b i j k, b j k -> b i k", weight, value)
        return feat



class PeriodicSetTransformerEncoder(nn.Module):
    def __init__(self, embedding_dim, num_heads, attention_dropout=0.0, dropout=0.0, activation=nn.Mish, use_va=False):
        super(PeriodicSetTransformerEncoder, self).__init__()
        if use_va:
            self.embedding = nn.Linear(embedding_dim, embedding_dim)
            self.out = nn.Linear(embedding_dim, embedding_dim)
        else:
            self.embedding = nn.Linear(embedding_dim, embedding_dim * num_heads)
            self.out = nn.Linear(embedding_dim * num_heads, embedding_dim)
        
        self.use_va = use_va

        # self.multihead_attention = MHA(embedding_dim, embedding_dim * num_heads, num_heads, dropout=attention_dropout)
        self.vector_attention = VectorAttention(embedding_dim,
                                                attention_dropout=attention_dropout,
                                                activation=activation)
        self.pre_norm = nn.LayerNorm(embedding_dim)
        self.ln = torch.nn.LayerNorm(embedding_dim)
        self.ffn = nn.Linear(embedding_dim, embedding_dim)
        self.ffn = nn.Sequential(nn.Linear(embedding_dim, embedding_dim),
                                 activation())

        self.pt_last = Point_Transformer_Last(embedding_dim)

    def forward(self, x, weights, use_weights=True):
        x_norm = self.ln(x)
        x_norm=x_norm.permute(0, 2, 1)    ####
        if self.use_va:
            att_output = self.vector_attention(x_norm, weights)
        else:
            # att_output = self.multihead_attention(x_norm, weights)
            att_output = self.pt_last(x_norm)
        att_output=att_output.permute(0, 2, 1)
        output1 = x + self.out(att_output)
        output2 = self.ln(output1)
        output2 = self.ffn(output2)
        return self.ln(output1 + output2)


class PeriodicSetTransformer(nn.Module):

    def __init__(self, str_fea_len, embed_dim, num_heads, n_encoders=3, decoder_layers=1, components=None,
                 expansion_size=10, dropout=0., attention_dropout=0., use_cuda=True, atom_encoding="mat2vec",
                 use_weighted_attention=True, use_weighted_pooling=True, activation=nn.Mish, sigmoid_out=False,
                 expand_distances=True):
        super(PeriodicSetTransformer, self).__init__()
        if components is None:
            components = ["pdd", "composition"]

        if atom_encoding not in ["mat2vec", "cgcnn"]:
            raise ValueError(f"atom_encoding_dim must be in {['mat2vec', 'cgcnn']}")
        else:
            atom_encoding_dim = 200 if atom_encoding == "mat2vec" else 92
            id_prop_file = "mat2vec.csv" if atom_encoding == "mat2vec" else "atom_init.json"

        self.composition = "composition" in components
        self.pdd_encoding = "pdd" in components
        self.use_weighted_attention = use_weighted_attention
        self.use_weighted_pooling = use_weighted_pooling
        self.expand_distances = expand_distances
        if self.expand_distances:
            self.pdd_embedding_layer = nn.Linear((str_fea_len - 1) * expansion_size, embed_dim)
        else:
            print("Not expanding distances")
            self.pdd_embedding_layer = nn.Linear(str_fea_len - 1, embed_dim)
        self.comp_embedding_layer = nn.Linear(atom_encoding_dim, embed_dim)
        self.dropout_layer = nn.Dropout(p=dropout)
        self.af = AtomFeaturizer(use_cuda=use_cuda, id_prop_file=id_prop_file)
        self.de = DistanceExpansion(size=expansion_size, use_cuda=use_cuda, out_size=expansion_size*(str_fea_len-1))
        self.ln = nn.LayerNorm(embed_dim)
        self.ln2 = nn.LayerNorm(embed_dim)
        self.cell_embed = nn.Linear(6, 32)
        self.softplus = nn.Softplus()
        self.encoders = nn.ModuleList(
            [PeriodicSetTransformerEncoder(embed_dim, num_heads, attention_dropout=attention_dropout, activation=activation) for _ in
             range(n_encoders)])
        self.decoder = nn.ModuleList([nn.Linear(embed_dim, embed_dim)
                                      for _ in range(decoder_layers - 1)])
        self.activations = nn.ModuleList([activation()
                                          for _ in range(decoder_layers - 1)])
        self.out = nn.Linear(embed_dim, 1)
        self.so = sigmoid_out
        self.sigmoid = nn.Sigmoid()

    def forward(self, features):
        str_fea, comp_fea, cell_fea = features
        weights = str_fea[:, :, 0, None]
        comp_features = self.af(comp_fea)
        comp_features = self.comp_embedding_layer(comp_features)
        comp_features = self.dropout_layer(comp_features)
        str_features = str_fea[:, :, 1:]

        if self.expand_distances:
            str_features = self.de(str_features)
        str_features = self.pdd_embedding_layer(str_features)

        if self.composition and self.pdd_encoding:
            x = comp_features + str_features
        elif self.composition:
            x = comp_features
        elif self.pdd_encoding:
            x = str_features
        x_init = x
        for encoder in self.encoders:
            x = encoder(x, weights, use_weights=self.use_weighted_attention)

        if self.use_weighted_pooling:
            x = torch.sum(weights * (x + x_init), dim=1)
        else:
            x = torch.mean(x + x_init, dim=1)

        x = self.ln2(x)
        for layer, activation in zip(self.decoder, self.activations):
            x = layer(x)
            x = activation(x)

        if self.so:
            return self.sigmoid(self.out(x))
        return self.out(x)


class PeSTEncoder(nn.Module):

    def __init__(self, str_fea_len, embed_dim, num_heads, n_encoders=3, expansion_size=10):
        super(PeSTEncoder, self).__init__()
        self.embedding_layer = nn.Linear((str_fea_len - 1) * expansion_size, embed_dim)
        self.comp_embedding_layer = nn.Linear(92, embed_dim)
        self.af = AtomFeaturizer()
        self.de = DistanceExpansion(size=expansion_size)
        self.ln = nn.LayerNorm(embed_dim)
        self.ln2 = nn.LayerNorm(embed_dim + 6)
        self.softplus = nn.Softplus()
        self.encoders = nn.ModuleList([PeriodicSetTransformerEncoder(embed_dim, num_heads) for _ in range(n_encoders)])

    def forward(self, features, pool=False):
        str_fea, comp_fea, cell_fea = features
        weights = str_fea[:, :, 0, None]
        comp_features = self.af(comp_fea)
        comp_features = self.comp_embedding_layer(comp_features)
        str_features = str_fea[:, :, 1:]
        str_features = self.embedding_layer(self.de(str_features))
        # x = comp_features + str_features
        x = self.ln(comp_features + str_features)
        for encoder in self.encoders:
            x = encoder(x, weights)

        if pool:
            return torch.sum(weights * x, dim=1)

        return weights, x


class AtomFeaturizer(nn.Module):
    def __init__(self, id_prop_file="mat2vec.csv", use_cuda=True):
        super(AtomFeaturizer, self).__init__()
        if id_prop_file == "mat2vec.csv":
            af = pd.read_csv("mat2vec.csv").to_numpy()[:, 1:].astype("float32")
            af = np.vstack([np.zeros(200), af, np.ones(200)])
        else:
            with open(id_prop_file) as f:
                atom_fea = json.load(f)
            af = np.vstack([i for i in atom_fea.values()])
            af = np.vstack([np.zeros(92), af, np.ones(92)])  # last is the mask, first is for padding
        if use_cuda:
            self.atom_fea = torch.Tensor(af).cuda()
        else:
            self.atom_fea = torch.Tensor(af)

    def forward(self, x):
        return torch.squeeze(self.atom_fea[x.long()])


class DistanceExpansion(nn.Module):
    def __init__(self, size=5, use_cuda=True, out_size=150):
        super(DistanceExpansion, self).__init__()
        self.size = size
        self.out_size = out_size
        if use_cuda:
            self.starter = torch.Tensor([i for i in range(size)]).cuda()
        else:
            self.starter = torch.Tensor([i for i in range(size)])
        self.starter /= size
        self.lin = nn.Sequential(nn.Linear(1, size), nn.Mish())
        self.lin2 = nn.Linear(out_size, out_size)
        self.ln = nn.LayerNorm(size)

    def forward(self, x):
        out = (1 - (x.flatten().reshape((-1, 1)) - self.starter)) ** 2
        return out.reshape((x.shape[0], x.shape[1], x.shape[2] * self.size))


class ElementMasker(nn.Module):
    def __init__(self):
        super(ElementMasker, self).__init__()

    def forward(self, input, masked_values, mask_type="composition"):
        x = input.clone()
        if mask_type == "composition":
            x[torch.arange(x.shape[0]), masked_values] = -1  # depends on AtomFeaturizer
        else:
            x[torch.arange(x.shape[0]), masked_values, 1:] = -1
        return x


class CompositionDecoder(nn.Module):

    def __init__(self, input_dim, predict_indv_props=True):
        super(CompositionDecoder, self).__init__()
        self.pip = predict_indv_props
        if predict_indv_props:
            self.dense = nn.Linear(input_dim, 92)
        else:
            self.dense = nn.Linear(input_dim, 100)
        self.group_num = nn.Softmax(dim=-1)
        self.period_num = nn.Softmax(dim=-1)
        self.electronegativity = nn.Softmax(dim=-1)
        self.cov_radius = nn.Softmax(dim=-1)
        self.val_electrons = nn.Softmax(dim=-1)
        self.first_ion = nn.Softmax(dim=-1)
        self.elec_aff = nn.Softmax(dim=-1)
        self.block = nn.Softmax(dim=-1)
        self.atomic_vol = nn.Softmax(dim=-1)
        self.element = nn.Softmax(dim=-1)

    def forward(self, x, masked_values):
        x = x[torch.arange(x.shape[0]), masked_values]
        embedded = self.dense(x)
        if self.pip:
            gn = embedded[:, :19]
            pn = embedded[:, 19:26]
            en = embedded[:, 26:36]
            cr = embedded[:, 36:46]
            ve = embedded[:, 46:58]
            fi = embedded[:, 58:68]
            ea = embedded[:, 68:78]
            bl = embedded[:, 78:82]
            av = embedded[:, 82:92]
            return gn, pn, en, cr, ve, fi, ea, bl, av
        else:
            element = self.element(embedded)
            return element


class DistanceDecoder(nn.Module):

    def __init__(self, input_dim, output_dim):
        super(DistanceDecoder, self).__init__()
        self.out = nn.Linear(input_dim, output_dim)

    def forward(self, x, masked_values):
        x = x[torch.arange(x.shape[0]), masked_values]
        return self.out(x)


class NeighborDecoder(nn.Module):
    def __init__(self, input_dim, output_dim):
        super(NeighborDecoder, self).__init__()
        self.fc = nn.Linear(input_dim, output_dim)

    def forward(self, x):
        return self.fc(x)


class FineTuner(nn.Module):
    def __init__(self, input_dim, num_heads=1, n_encoders=1, attention_dropout=0.0, dropout=0.0):
        super(FineTuner, self).__init__()
        self.encoders = nn.ModuleList(
            [PeriodicSetTransformerEncoder(input_dim, num_heads, attention_dropout=attention_dropout) for _ in
             range(n_encoders)])
        self.embed = nn.Linear(input_dim, input_dim)
        self.dropout_layer = nn.Dropout(p=dropout)
        self.sp = nn.Softplus()
        self.ln = nn.LayerNorm(input_dim)
        self.out = nn.Linear(input_dim, 1)

    def forward(self, x, weights=None):
        if weights is not None:
            for encoder in self.encoders:
                x = self.ln(x + encoder(x, weights))
            x = torch.sum(weights * x, dim=1)
        x = self.dropout_layer(x)
        x = self.sp(self.embed(x))
        return self.out(x)
