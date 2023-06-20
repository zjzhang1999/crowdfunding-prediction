import cv2
import torch
import librosa
from PIL import Image
import torch.nn as nn
from torchvision import transforms
from transformers import BertTokenizer, BertModel
from torchvision.models.video import r3d_18
# from d2l import torch as d2l
import matplotlib.pyplot as plt
import torchaudio
from moviepy.editor import AudioFileClip
from tqdm import tqdm
import numpy as np
from torchvision.io import read_video
import os
bs = 2
device_default = '0,'
# model1:2fc_10epoch_8batch_0.001lr_10hiddensize
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
device_ids = [int(i) for i in device_default.split(',') if i!=''] # 10卡机
batchSize = bs * len(device_ids)


# %%
class TextFeatureExtractor:
    def __init__(self, max_seq_length=512, model_name='bert-base-uncased'):
        self.tokenizer = BertTokenizer.from_pretrained(model_name)
        self.model = BertModel.from_pretrained(model_name)
        self.model =  torch.nn.DataParallel(self.model, device_ids=device_ids) # 指定要用到的设备
        self.model = self.model.to(device)
        self.max_seq_length = max_seq_length

    def __call__(self, text_path):
        tokens = self.pad_truncate_sequence(text_path, self.max_seq_length).to(device)
        with torch.no_grad():
            outputs = self.model(tokens)
            embeddings = outputs[1]
        return embeddings

    def pad_truncate_sequence(self, text_path, max_seq_length=512):
        with open(text_path, 'r', encoding='utf-8') as f:
            text = f.read()
        tokens = self.tokenizer.encode(text, add_special_tokens=True)
        if len(tokens) <= max_seq_length:
            tokens += [0] * (max_seq_length - len(tokens))
        else:
            tokens = tokens[:max_seq_length]
        tokens_tensor = torch.tensor(tokens)
        tokens = tokens_tensor.unsqueeze(0)
        return tokens


# %%
class AudioFeatureExtractor(nn.Module):
    def __init__(self, sr=16000, n_fft=2048, hop_length=512, n_mfcc=20):
        super(AudioFeatureExtractor, self).__init__()
        self.sr = sr
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.n_mfcc = n_mfcc

    def forward(self, video_path):
        root = video_path.split('.mp4')[0]
        wav_name = root + '.wav'
        if not os.path.exists(wav_name):
            my_audio_clip = AudioFileClip(video_path)
            my_audio_clip.write_audiofile(wav_name)
        audio, _ = librosa.load(wav_name, sr=self.sr)
        mfcc = librosa.feature.mfcc(y=audio, sr=self.sr, n_fft=self.n_fft, hop_length=self.hop_length,
                                    n_mfcc=self.n_mfcc)
        mfcc_tensor = torch.from_numpy(mfcc).to(device)
        mfcc_mean = torch.mean(mfcc_tensor, dim=1)
        mfcc_mean = mfcc_mean.view(1, -1)
        return mfcc_mean


# %%
class VideoFeatureExtractor:
    def __init__(self):
        self.video_model = r3d_18(pretrained=True)
        self.video_model = torch.nn.DataParallel(self.video_model, device_ids=device_ids)  # 指定要用到的设备
        self.video_model = self.video_model.to(device)
        self.transform = transforms.Compose([
            # transforms.ToPILImage(),
            transforms.Resize((128, 171), antialias=True),
            transforms.CenterCrop((112, 112)),
            # transforms.ToTensor(),
            transforms.Normalize((0.43216, 0.394666, 0.37645), (0.22803, 0.22145, 0.216989))
        ])

    def extract_features(self, video_path):
        video_frames = []
        video = read_video(video_path,output_format='TCHW',end_pts=100,pts_unit='sec')[0]
        count = 1500
        images = video[:count, ...]
        images = images.to(device).type(torch.float32).permute(0, 3, 1, 2)
        for image in images:
            image = self.transform(image)
            video_frames.append(image)
        # vidcap = cv2.VideoCapture(video_path)
        # success, image = vidcap.read()
        # count = 0
        # while success and count < 1500:
        #     pil_image = Image.fromarray(image).convert('RGB')
        #     pil_image = pil_image.resize((128, 171))
        #     transformed_image = self.transform(pil_image)
        #     video_frames.append(transformed_image)
        #     success, image = vidcap.read()
        #     count += 1
        num_frames = len(video_frames)
        if num_frames % 16 != 0:
            num_needed_frames = 16 - (num_frames % 16)
            for i in range(num_needed_frames):
                video_frames.append(video_frames[-1])
        video_tensor = torch.stack(video_frames)#.to(device)
        video_tensor = video_tensor.permute(1, 0, 2, 3)
        with torch.no_grad():
            video_feature = self.video_model(video_tensor[None]).squeeze()
        return video_feature.unsqueeze(0)


# %%
import os
import pandas as pd
from torch.utils.data import Dataset
from PIL import Image


class MultimodalDataset(Dataset):
    def __init__(self, root_dir, max_seq_length=512, is_train=True, device=None):
        self.root_dir = root_dir
        self.max_seq_length = max_seq_length
        self.text_extractor = TextFeatureExtractor()
        self.video_extractor = VideoFeatureExtractor()
        self.audio_extractor = AudioFeatureExtractor()
        self.device = device

        # Load the labels for each sample from a CSV file
        if is_train == True:
            dataFrame = pd.read_csv('train.csv').values[:, 1:]
        else:
            dataFrame = pd.read_csv('test.csv').values[:, 1:]
        self.samples = []
        for dataPath, label in tqdm(dataFrame):
            if not os.path.isdir(os.path.join(dataPath)):
                continue

            video_path = None
            text_path = None
            for filename in os.listdir(os.path.join(dataPath)):
                if filename.endswith('.mp4'):
                    video_path = os.path.join(dataPath, filename)
                elif filename.endswith('.txt'):
                    text_path = os.path.join(dataPath, filename)
            if video_path is None or text_path is None:
                continue

            # Append the sample to the appropriate list based on the is_train flag
            self.samples.append((video_path, text_path, label))

        # labels_file = os.path.join(root_dir, 'labels.csv')
        # labels_df = pd.read_csv(labels_file)
        # if is_train == True:
        #     root_dir = os.path.join(root_dir, 'train')
        # else:
        #     root_dir = os.path.join(root_dir, 'test')
        # self.samples = []
        # for subdir in os.listdir(root_dir):
        #     if not os.path.isdir(os.path.join(root_dir, subdir)):
        #         continue
        #     video_path = None
        #     text_path = None
        #     for filename in os.listdir(os.path.join(root_dir, subdir)):
        #         if filename.endswith('.mp4'):
        #             video_path = os.path.join(root_dir, subdir, filename)
        #         elif filename.endswith('.txt'):
        #             text_path = os.path.join(root_dir, subdir, filename)
        #     if video_path is None or text_path is None:
        #         continue
        #
        #     # Find the label for the current sample
        #     video_filename_with_ext = os.path.basename(video_path)
        #     video_filename, _ = os.path.splitext(video_filename_with_ext)
        #     label = labels_df.loc[labels_df['video_filename'] == video_filename, 'label'].values[0]
        #
        #     # Append the sample to the appropriate list based on the is_train flag
        #     self.samples.append((video_path, text_path, label))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        video_path, text_path, label = self.samples[idx]

        text_features = self.text_extractor(text_path).to(self.device)
        video_features = self.video_extractor.extract_features(video_path).to(self.device)
        audio_features = self.audio_extractor(video_path).to(self.device)

        # Concatenate the features and return them with the label
        features = torch.cat((video_features, audio_features, text_features), dim=1)
        return features, torch.tensor(label)


# %%
from torch.utils.data import DataLoader

train_dataset = MultimodalDataset(root_dir='./', is_train=True, device=device)
test_dataset = MultimodalDataset(root_dir='./', is_train=False, device=device)
train_dataloader = DataLoader(train_dataset, batch_size=batchSize, shuffle=True)
test_dataloader = DataLoader(test_dataset, batch_size=batchSize, shuffle=False)


# %%
class Net(nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        self.fc1 = nn.Linear(1188, 10)
        self.fc2 = nn.Linear(10, 2)

    def forward(self, x):
        x = torch.relu(self.fc1(x))
        x = self.fc2(x)
        return x


model = Net()
model = torch.nn.DataParallel(model, device_ids=device_ids)  # 指定要用到的设备
model = model.to(device)

num_epochs = 10
optimizer = torch.optim.SGD(model.parameters(), lr=0.001)

# 存储每个epoch的损失和准确率
train_losses = []
train_accs = []
test_accs = []
criterion = nn.CrossEntropyLoss().to(device)

# 迭代mini-batch
for epoch in range(num_epochs):
    train_loss = 0.0
    train_acc = 0.0
    for i, data in enumerate(tqdm(train_dataloader)):
        # 获取一个mini-batch
        inputs, labels = data[0], data[1]
        inputs = inputs.to(device)
        labels = labels.to(device)

        # 清空梯度
        optimizer.zero_grad()

        # 将输入传入模型
        outputs = model(inputs)

        # 计算损失和准确率

        loss = criterion(outputs.squeeze(), labels)
        _, preds = torch.max(outputs.squeeze(), 1)
        acc = torch.sum(preds == labels.data).detach().cpu().numpy() / inputs.size(0)

        # 反向传播
        loss.backward()
        optimizer.step()

        # 统计损失和准确率
        train_loss += loss.detach().cpu().numpy() * inputs.size(0)
        train_acc += acc * inputs.size(0)

        # 测试模型
    model.eval()
    test_acc = 0.0
    with torch.no_grad():
        for i, data in enumerate(tqdm(test_dataloader)):
            # 获取一个mini-batch
            inputs, labels = data[0], data[1]
            inputs = inputs.to(device)
            labels = labels.to(device)
            # 计算网络输出
            outputs = model(inputs)
            _, preds = torch.max(outputs.squeeze(), 1)
            acc = torch.sum(preds == labels.data).detach().cpu().numpy() / inputs.size(0)
            # 统计预测准确率
            test_acc += acc * inputs.size(0)

    # 计算该epoch的平均损失和准确率
    epoch_loss = train_loss / len(train_dataset)
    epoch_acc = train_acc / len(train_dataset)
    epoch_acc2 = test_acc / len(test_dataset)

    # 记录该epoch的损失和准确率
    train_losses.append(epoch_loss)
    train_accs.append(epoch_acc)
    test_accs.append(epoch_acc2)

    # 输出该epoch的平均损失和准确率
    print('Epoch %d loss: %.3f acc: %.3f  testacc:%.3f' % (epoch + 1, epoch_loss, epoch_acc, epoch_acc2))

# 绘制损失和准确率随着epoch的变化曲线
plt.plot(train_losses, label='train loss')
plt.plot(train_accs, label='train acc')
plt.plot(test_accs, label='test acc')
plt.xlabel('Epoch')
plt.legend()
plt.savefig('2fc_10epoch_8batch_0.001lr_10hidden.png')


