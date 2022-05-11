import torch
import torch.nn as nn
from torchvision.models import vgg16
from torchvision.ops import RoIPool
import numpy as np
from utils import propose_region
from model.rpn import RPN
from anchor import FRCNNAnchorMaker
import time


class FRCNNHead(nn.Module):
    def __init__(self, num_classes, roi_output_size=7):
        super().__init__()
        self.num_classes = num_classes
        self.cls_head = nn.Linear(4096, num_classes)
        self.reg_head = nn.Linear(4096, 4)
        self.roi_pool = RoIPool(output_size=(roi_output_size, roi_output_size), spatial_scale=1.)
        self.fc = nn.Sequential(nn.Linear(512 * 7 * 7, 4096),
                                nn.ReLU(inplace=True),
                                nn.Linear(4096, 4096),
                                nn.ReLU(inplace=True)
                                )
        self.initialize()

    def initialize(self):
        for c in self.cls_head.children():
            if isinstance(c, nn.Linear):
                nn.init.normal_(c.weight, std=0.01)
                nn.init.constant_(c.bias, 0)
        for c in self.reg_head.children():
            if isinstance(c, nn.Linear):
                nn.init.normal_(c.weight, std=0.01)
                nn.init.constant_(c.bias, 0)
        for c in self.fc.children():
            if isinstance(c, nn.Linear):
                nn.init.normal_(c.weight, std=0.01)
                nn.init.constant_(c.bias, 0)

    def forward(self, features, rois):

        device = rois.get_device()
        filtered_rois_numpy = np.array(rois.detach().cpu()).astype(np.float32)

        # 3. scale original box w, h to feature w, h
        filtered_rois_numpy[:, ::2] = filtered_rois_numpy[:, ::2] * features.size(3)
        filtered_rois_numpy[:, 1::2] = filtered_rois_numpy[:, 1::2] * features.size(2)

        # 4. convert numpy boxes to list of tensors
        filtered_boxes_tensor = [torch.FloatTensor(filtered_rois_numpy).to(device)]

        # --------------- RoI Pooling --------------- #
        x = self.roi_pool(features, filtered_boxes_tensor)           # [2000, 512, 7, 7]
        x = x.view(x.size(0), -1)                                      # 2000, 512 * 7 * 7

        # --------------- forward head --------------- #
        x = self.fc(x)                                                 # 2000, 4096
        cls = self.cls_head(x)                                         # 2000, 21
        reg = self.reg_head(x)                                         # 2000, 21 * 4
        frcnn_cls = cls.view(1, -1, self.num_classes)                        # [1, 2000, 21]
        frcnn_reg = reg.view(1, -1, 4)                    # [1, 2000, 21 * 4]
        return frcnn_cls, frcnn_reg


class FRCNN(nn.Module):
    def __init__(self):
        super().__init__()

        # ** for forward
        self.extractor = nn.Sequential(*list(vgg16(pretrained=True).features.children())[:-1])
        self.rpn = RPN()
        self.head = FRCNNHead(num_classes=21)

        # ** for anchor
        self.anchor_maker = FRCNNAnchorMaker()
        self.anchor_base = self.anchor_maker.generate_anchor_base()

    def forward(self, x):
        # x : image [B, 3, H, W]
        features = self.extractor(x)

        # each image has different anchor
        anchor = self.anchor_maker._enumerate_shifted_anchor(self.anchor_base, origin_image_size=x.size()[2:])
        anchor = torch.from_numpy(anchor).to(x.get_device())  # assign device
        cls_rpn, reg_rpn, rois = self.rpn(features, anchor, mode='train')
        cls_frcnn, reg_frcnn = self.head(features, rois)

        return cls_rpn, reg_rpn, cls_frcnn, reg_frcnn, anchor


if __name__ == '__main__':

    tic = time.time()
    frcnn = FRCNN().cuda()
    print("model cuda time :", time.time() - tic)

    tic = time.time()
    img1 = torch.randn([1, 3, 600, 1000]).cuda()  # 37, 62
    print("image cuda time :", time.time() - tic)

    tic = time.time()
    rpn_cls, rpn_reg, frcnn_cls, frcnn_reg, anchor = frcnn(img1)

    print(rpn_cls.size())     # torch.Size([1, 18, 37, 62])
    print(rpn_reg.size())     # torch.Size([1, 36, 37, 62])
    print(frcnn_cls.size())   # torch.Size([1, 1988, 21])
    print(frcnn_reg.size())   # torch.Size([1, 1988, 4])
    print(anchor.size())      # torch.Size([20646, 4])

