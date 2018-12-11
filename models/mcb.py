"""
Implementation of MultiLinear Compact Bilinear Pooling model
[https://arxiv.org/pdf/1606.01847.pdf]
"""

import numpy as np
import torch
from torch import nn
import torch.nn.functional as F
from models import extractor


class MulitmodalCompactBilinearPool(nn.Module):
    """
    Multimodal Compact Bilinear Pooling Module
    """
    def __init__(self, original_dim, projection_dim, n_modalities=2):
        super(MulitmodalCompactBilinearPool, self).__init__()

        self.C = []
        self.n_modalities = n_modalities

        for _ in range(n_modalities):
            # C tensor performs the mapping of the h vector and stores the s vector values as well
            C = torch.zeros(original_dim, projection_dim)
            for i in range(original_dim):
                C[i, np.random.randint(0, projection_dim-1)] = 2 * np.random.randint(0, 2) - 1  # s values

                if torch.cuda.is_available():
                    C = C.cuda()

            self.C.append(C)

    def forward(self, *x):
        feature_size = x[0].size()
        y = [0]*self.n_modalities

        for i, d in enumerate(x):
            y[i] = d.mm(self.C[i]).view(feature_size[0], -1)

        phi = y[0]
        signal_sizes = y[0].size()[1:]  # signal_sizes should not have batch dimension as per docs

        for i in range(1, self.n_modalities):
            i_fft = torch.rfft(phi, 1)
            j_fft = torch.rfft(y[i], 1)

            # element wise multiplication
            x = i_fft.mul(j_fft)

            # inverse FFT
            phi = torch.irfft(x, 1, signal_sizes=signal_sizes)

        return phi


class MCBModel(nn.Module):
    """
    The model from https://arxiv.org/pdf/1606.01847.pdf
    """
    def __init__(self, vocab_size, embed_dim=300,
                 image_dim=2048, hidden_dim=1024,
                 mcb_dim=16000, output_dim=1000,
                 raw_images=True):
        super(MCBModel, self).__init__()

        # Flag to indicate use of raw images
        self.raw_images = raw_images

        # MCB model uses ResNet-152
        # self.feature_extractor = extractor.FeatureExtractor("resnet152")
        # self.feature_extractor = torch.load('/data/work/huaminz2/VQA_torch/resnet152-b121ed2d.pth')
        self.embedding = nn.Sequential(
            nn.Linear(vocab_size, embed_dim),
            nn.Tanh())

        self.num_rnn_layers = 2
        self.num_directions = 1
        self.rnn = nn.LSTM(embed_dim, hidden_dim, num_layers=self.num_rnn_layers, batch_first=True)

        self.mcb_dim = mcb_dim
        self.mcb = MulitmodalCompactBilinearPool(image_dim, mcb_dim, n_modalities=2)

        self.attention = nn.Sequential(
            nn.Conv2d(mcb_dim, 512, kernel_size=1),
            nn.ReLU(),
            nn.Conv2d(512, 1, kernel_size=1),
            nn.Softmax(dim=1)
        )
        self.classification = nn.Linear(mcb_dim, output_dim)

    def forward(self, img, ques):
        if False: #self.raw_images:
            img_feat = self.feature_extractor(img)
        else:
            img_feat = img
        img_feat_norm = img_feat / torch.norm(img_feat, p=2).detach()

        q = self.embedding(ques)  # BxTxD

        _, hidden = self.rnn(q)

        hidden_state, _ = hidden
        h1 = hidden_state[0, :, :]  # BxD
        h2 = hidden_state[1, :, :]  # BxD

        ques_embed = torch.cat([h1, h2], dim=1)  # BxD2

        ## Perform attention computation

        # replicate question embedding from BxD to BxDxHxW and then convert to B'xD2
        # B' is each slice rolled out
        ques_tiled = ques_embed.view(ques_embed.size(0), ques_embed.size(1), 1, 1)
        ques_tiled = ques_tiled.repeat(1, 1, img_feat.size(-2), img_feat.size(-1))  # BxDxHxW
        ques_tiled = ques_tiled.permute(0, 2, 3, 1).contiguous().view(-1, ques_embed.size(1))

        # convert BxCxHxW to B'xC where B' is now each slice of the feature map
        img_pre_mcb = img_feat_norm.permute(0, 2, 3, 1).contiguous().view(-1, img_feat.size(1))

        att_x = self.mcb(img_pre_mcb, ques_tiled)  # B'xM  M is MCB dim
        att_x = att_x.view(img_feat.size(0), img_feat.size(2), img_feat.size(3), self.mcb_dim)  # BxHxWxM
        att_x = att_x.permute(0, 3, 1, 2).contiguous()  # BxMxHxW

        att_x = self.attention(att_x)

        img_att_x = img_feat_norm.mul(att_x.repeat(1, img_feat.size(1), 1, 1))
        img_att_x = img_att_x.sum(dim=3).sum(dim=2)

        # combine attended visual features and question embedding
        x = self.mcb(img_att_x, ques_embed)

        # signed square root
        y = torch.sqrt(F.relu(x)) - torch.sqrt(F.relu(-x))
        # L2 normalization
        y = y / torch.norm(y, p=2).detach()
        y = self.classification(y)

        return y


