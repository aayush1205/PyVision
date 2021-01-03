import torch.nn as nn 
import torch  

import math 

from efficientnet_pytorch import EfficientNet as EffNet  
from .utils import BBoxTransform, ClipBoxes, Anchors
from .losses import FocalLoss
from  torchvision.ops.boxes import nms as torch_nms 

def nms(dets, thresh):
    return torch_nms(
        dets[:, :4], dets[:, 4], thresh
    )

class ConvBlock(nn.Module):

    def __init__(self, num_channels):
        
        super(ConvBlock, self).__init__()

        self.conv = nn.Sequential(
            nn.Conv2d(num_channels, num_channels, kernel_size=3, stride=1, padding=1, groups=num_channels), 
            nn.Conv2d(num_channels, num_channels, kernel_size=1, stride=1, padding=0), 
            nn.BatchNorm2d(num_features=num_channels, momentum=0.9997, eps=4e-5), 
            nn.ReLU()
        )
        
    def forward(self, x):
        
        return self.conv(x)

class BiFPN(nn.Module):

    def __init__(self, num_channels, eps=1e-4):

        super(BiFPN, self).__init__()

        self.eps = eps

        # Here, we define the various conv layers
        self.conv6_up = ConvBlock(num_channels)
        self.conv5_up = ConvBlock(num_channels)
        self.conv4_up = ConvBlock(num_channels)
        self.conv3_up = ConvBlock(num_channels)
        self.conv4_down = ConvBlock(num_channels)
        self.conv5_down = ConvBlock(num_channels)
        self.conv6_down = ConvBlock(num_channels)
        self.conv7_down = ConvBlock(num_channels)

        # Feature scaling layers
        self.p6_upsample = nn.Upsample(scale_factor=2, mode='nearest')
        self.p5_upsample = nn.Upsample(scale_factor=2, mode='nearest')
        self.p4_upsample = nn.Upsample(scale_factor=2, mode='nearest')
        self.p3_upsample = nn.Upsample(scale_factor=2, mode='nearest')

        self.p4_downsample = nn.MaxPool2d(kernel_size=2)
        self.p5_downsample = nn.MaxPool2d(kernel_size=2)
        self.p6_downsample = nn.MaxPool2d(kernel_size=2)
        self.p7_downsample = nn.MaxPool2d(kernel_size=2)

        # Weight
        self.p6_w1 = nn.Parameter(torch.ones(2))
        self.p6_w1_relu = nn.ReLU()
        self.p5_w1 = nn.Parameter(torch.ones(2))
        self.p5_w1_relu = nn.ReLU()
        self.p4_w1 = nn.Parameter(torch.ones(2))
        self.p4_w1_relu = nn.ReLU()
        self.p3_w1 = nn.Parameter(torch.ones(2))
        self.p3_w1_relu = nn.ReLU()

        self.p4_w2 = nn.Parameter(torch.ones(3))
        self.p4_w2_relu = nn.ReLU()
        self.p5_w2 = nn.Parameter(torch.ones(3))
        self.p5_w2_relu = nn.ReLU()
        self.p6_w2 = nn.Parameter(torch.ones(3))
        self.p6_w2_relu = nn.ReLU()
        self.p7_w2 = nn.Parameter(torch.ones(2))
        self.p7_w2_relu = nn.ReLU()        

   
    def forward(self, inputs):
        """
            P7_0 -------------------------- P7_2 -------->
            P6_0 ---------- P6_1 ---------- P6_2 -------->
            P5_0 ---------- P5_1 ---------- P5_2 -------->
            P4_0 ---------- P4_1 ---------- P4_2 -------->
            P3_0 -------------------------- P3_2 -------->
        """

        # P3_0, P4_0, P5_0, P6_0 and P7_0
        p3_in, p4_in, p5_in, p6_in, p7_in = inputs[0], inputs[1], inputs[2], inputs[3], inputs[4]
        
        # P7_0 to P7_2
        # Weights for P6_0 and P7_0 to P6_1
        p6_w1 = self.p6_w1_relu(self.p6_w1)
        weight = p6_w1 / (torch.sum(p6_w1, dim=0) + self.eps)
        
        # Connections for P6_0 and P7_0 to P6_1 respectively
        p6_up = self.conv6_up(weight[0] * p6_in + weight[1] * self.p6_upsample(p7_in))
        
        # Weights for P5_0 and P6_0 to P5_1
        p5_w1 = self.p5_w1_relu(self.p5_w1)
        weight = p5_w1 / (torch.sum(p5_w1, dim=0) + self.eps)
        
        # Connections for P5_0 and P6_0 to P5_1 respectively
        p5_up = self.conv5_up(weight[0] * p5_in + weight[1] * self.p5_upsample(p6_up))
        
        # Weights for P4_0 and P5_0 to P4_1
        p4_w1 = self.p4_w1_relu(self.p4_w1)
        weight = p4_w1 / (torch.sum(p4_w1, dim=0) + self.eps)
        
        # Connections for P4_0 and P5_0 to P4_1 respectively
        p4_up = self.conv4_up(weight[0] * p4_in + weight[1] * self.p4_upsample(p5_up))

        # Weights for P3_0 and P4_1 to P3_2
        p3_w1 = self.p3_w1_relu(self.p3_w1)
        weight = p3_w1 / (torch.sum(p3_w1, dim=0) + self.eps)
        
        # Connections for P3_0 and P4_1 to P3_2 respectively
        p3_out = self.conv3_up(weight[0] * p3_in + weight[1] * self.p3_upsample(p4_up))

        # Weights for P4_0, P4_1 and P3_2 to P4_2
        p4_w2 = self.p4_w2_relu(self.p4_w2)
        weight = p4_w2 / (torch.sum(p4_w2, dim=0) + self.eps)
        
        # Connections for P4_0, P4_1 and P3_2 to P4_2 respectively
        p4_out = self.conv4_down(
            weight[0] * p4_in + weight[1] * p4_up + weight[2] * self.p4_downsample(p3_out))
        
        # Weights for P5_0, P5_1 and P4_2 to P5_2
        p5_w2 = self.p5_w2_relu(self.p5_w2)
        weight = p5_w2 / (torch.sum(p5_w2, dim=0) + self.eps)
        
        # Connections for P5_0, P5_1 and P4_2 to P5_2 respectively
        p5_out = self.conv5_down(
            weight[0] * p5_in + weight[1] * p5_up + weight[2] * self.p5_downsample(p4_out))
        
        # Weights for P6_0, P6_1 and P5_2 to P6_2
        p6_w2 = self.p6_w2_relu(self.p6_w2)
        weight = p6_w2 / (torch.sum(p6_w2, dim=0) + self.eps)
        
        # Connections for P6_0, P6_1 and P5_2 to P6_2 respectively
        p6_out = self.conv6_down(
            weight[0] * p6_in + weight[1] * p6_up + weight[2] * self.p6_downsample(p5_out))
        
        # Weights for P7_0 and P6_2 to P7_2
        p7_w2 = self.p7_w2_relu(self.p7_w2)
        weight = p7_w2 / (torch.sum(p7_w2, dim=0) + self.eps)
        
        # Connections for P7_0 and P6_2 to P7_2
        p7_out = self.conv7_down(weight[0] * p7_in + weight[1] * self.p7_downsample(p6_out))

        return p3_out, p4_out, p5_out, p6_out, p7_out


class Regressor(nn.Module):
    
    def __init__(self, in_channels, num_anchors, num_layers):
        
        super(Regressor, self).__init__()
        
        layers = []
        for _ in range(num_layers):
            layers.append(nn.Conv2d(in_channels, in_channels, kernel_size=3, stride=1, padding=1))
            layers.append(nn.ReLU(True))
        
        self.layers = nn.Sequential(*layers)
        self.header = nn.Conv2d(in_channels, num_anchors * 4, kernel_size=3, stride=1, padding=1)

    def forward(self, inputs):
        
        inputs = self.layers(inputs)
        inputs = self.header(inputs)
        output = inputs.permute(0, 2, 3, 1)
        
        return output.contiguous().view(output.shape[0], -1, 4)
    
class Classifier(nn.Module):

    def __init__(self, in_channels, num_anchors, num_classes, num_layers):

        super(Classifier, self).__init__()

        self.num_anchors = num_anchors
        self.num_classes = num_classes
        
        layers = []
        for _ in range(num_layers):
            layers.append(
                nn.Conv2d(in_channels, in_channels, kernel_size=3, stride=1, padding=1)
            )
            layers.append(
                nn.ReLU(True)
            )
        
        self.layers = nn.Sequential(*layers)
        self.header = nn.Conv2d(in_channels, num_anchors*num_classes, kernel_size=3, stride=1, padding=1)
        self.activation = nn.Sigmoid()

    def forward(self, x):

        x = self.layers(x)
        x = self.header(x)
        x = self.activation(x)

        x = x.permute(0, 2, 3, 1)

        output = x.contiguous().view(
            x.shape[0], x.shape[1], x.shape[2], self.num_anchors, self.num_classes
        )

        return output.contiguous().view(
            output.shape[0], -1, self.num_classes
        )
    

class EfficientNet(nn.Module):

    def __init__(self, model_coeff=0, pretrained=False):

        super(EfficientNet, self).__init__()

        if model_coeff not in [0, 1, 2, 3, 4, 5, 6, 7]:
            raise ValueError(f"{model_coeff} not a valid model. Models supported are b0, b1, b2, b3, b4, b5, b6, b7")
        model_version = f"efficientnet-b{model_coeff}"

        if pretrained:
            model = EffNet.from_pretrained(model_version) # change to local load later
        else:
            model = EffNet.from_name(model_version)
        
        del model._conv_head
        del model._bn1
        del model._avg_pooling
        del model._dropout
        del model._fc

        self.model = model 
    
    def forward(self, x):

        x = self.model._swish(self.model._bn0(self.model._conv_stem(x)))
        
        feature_maps = []
        for idx, block in enumerate(self.model._blocks):
            
            drop_connect_rate = self.model._global_params.drop_connect_rate
            if drop_connect_rate:
                drop_connect_rate *= float(idx) / len(self.model._blocks)
            
            x = block(x, drop_connect_rate=drop_connect_rate)
            
            if block._depthwise_conv.stride == [2, 2]:
                feature_maps.append(x)

        return feature_maps[1:]


class EfficientDet(nn.Module):

    def __init__(self, num_anchors=9, num_classes=20, model_coeff=0, focal_alpha=0.25, focal_gamma=2, pretrained=False, device="cuda"):

        super(EfficientDet, self).__init__()

        self.model_coeff = model_coeff
        self.num_classes = num_classes
        self.num_anchors = num_anchors
        self.focal_alpha = focal_alpha
        self.focal_gamma = focal_gamma
        self.device = device
        
        self.num_channels = [64, 88, 112, 160, 224, 288, 384, 384][self.model_coeff]
        
        # model specific conv layer configurations
        in_channels = [
            (40, 80, 192, 192),     #b0
            (40, 80, 192, 192),     #b1
            (48, 88, 208, 208),     #b2
            (48, 96, 232, 232),     #b3
            (56, 112, 272, 272),    #b4
            (64, 128, 304, 304),    #b5
            (72, 144, 344, 344),    #b6
            (80, 160, 384, 384)     #b7
        ]

        self.conv3 = nn.Conv2d(in_channels[self.model_coeff][0], self.num_channels, kernel_size=1, stride=1, padding=0)
        self.conv4 = nn.Conv2d(in_channels[self.model_coeff][1], self.num_channels, kernel_size=1, stride=1, padding=0)
        self.conv5 = nn.Conv2d(in_channels[self.model_coeff][2], self.num_channels, kernel_size=1, stride=1, padding=0)
        self.conv6 = nn.Conv2d(in_channels[self.model_coeff][3], self.num_channels, kernel_size=3, stride=2, padding=1)
        self.conv7 = nn.Sequential(
            nn.ReLU(), 
            nn.Conv2d(self.num_channels, self.num_channels, kernel_size=3, stride=2, padding=1)
        )

        self.bifpn = nn.Sequential(*[BiFPN(self.num_channels) for _ in range(min(2+self.model_coeff, 8))])

        self.regressor = Regressor(
            in_channels=self.num_channels, 
            num_anchors=self.num_anchors, 
            num_layers=3 + self.model_coeff // 3
        )

        self.classifier = Classifier(
            in_channels=self.num_channels, 
            num_anchors=self.num_anchors, 
            num_classes=self.num_classes, 
            num_layers=3 + self.model_coeff // 3
        )

        self.anchors = Anchors()
        self.regressBoxes = BBoxTransform()
        self.clipBoxes = ClipBoxes()
        self.focalloss = FocalLoss(alpha=self.focal_alpha, gamma=self.focal_gamma, device=self.device)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()

        prior = 0.01
        self.classifier.header.weight.data.fill_(0)
        self.classifier.header.bias.data.fill_(-math.log((1.0 - prior) / prior))

        self.regressor.header.weight.data.fill_(0)
        self.regressor.header.bias.data.fill_(0)

        self.backbone_net = EfficientNet(model_coeff=self.model_coeff, pretrained=pretrained)


    def freeze_bn(self):
        
        for m in self.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.eval()

    def forward(self, x):

        if len(x) == 2:
            is_training = True 
            imgs, annots = x 
        else:
            is_training = False
            imgs = x 

        c3, c4, c5 = self.backbone_net(imgs)
        p3 = self.conv3(c3)
        p4 = self.conv4(c4)
        p5 = self.conv5(c5)
        p6 = self.conv6(c5)
        p7 = self.conv7(p6)

        features = [p3, p4, p5, p6, p7]
        features = self.bifpn(features)

        regression = torch.cat([self.regressor(feature) for feature in features], dim=1)
        classification = torch.cat([self.classifier(feature) for feature in features], dim=1)
        anchors = self.anchors(imgs)

        if is_training:
            return self.focalloss(classification, regression, anchors, annots)
        else:

            transformed_anchors = self.regressBoxes(anchors, regression)
            transformed_anchors = self.clipBoxes(transformed_anchors, imgs)

            scores = torch.max(classification, dim=2, keepdim=True)[0]
            
            scores_gt_thresh = (scores > 0.05)[0, :, 0]
            if scores_gt_thresh.sum() == 0:
                return [
                    torch.zeros(0), 
                    torch.zeros(0), 
                    torch.zeros(0, 4)
                ]

            classification = classification[:, scores_gt_thresh, :]
            transformed_anchors = transformed_anchors[:, scores_gt_thresh, :]
            scores = scores[:, scores_gt_thresh, :]

            anchors_nms_idx = nms(torch.cat([transformed_anchors, scores], dim=2)[0, :, :], 0.5)

            nms_scores, nms_classes = classification[0, anchors_nms_idx, :].max(dim=1)
            
            return [nms_scores, nms_classes, transformed_anchors[0, anchors_nms_idx, :]]        



if __name__ == '__main__':
    
    def count_parameters(model):
        return sum(p.numel() for p in model.parameters() if p.requires_grad)

    model = EfficientDet(num_classes=80)
    print (count_parameters(model))


