import os
os.environ['CUDA_VISIBLE_DEVICES'] = '0,1'
import time
import logging
import warnings
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.backends.cudnn as cudnn
from torch.utils.data import DataLoader
from optparse import OptionParser
from torchvision.datasets import ImageFolder
from torchvision import transforms, utils,datasets
from utils import accuracy
from models import *
from dataset import *


def main():
    parser = OptionParser()
    parser.add_option('-j', '--workers', dest='workers', default=16, type='int',
                      help='number of data loading workers (default: 16)')
    parser.add_option('-e', '--epochs', dest='epochs', default=80, type='int',
                      help='number of epochs (default: 80)')
    parser.add_option('-b', '--batch-size', dest='batch_size', default=16, type='int',
                      help='batch size (default: 16)')
    parser.add_option('-c', '--ckpt', dest='ckpt', default=False,
                      help='load checkpoint model (default: False)')
    parser.add_option('-v', '--verbose', dest='verbose', default=100, type='int',
                      help='show information for each <verbose> iterations (default: 100)')
    parser.add_option('--lr', '--learning-rate', dest='lr', default=1e-3, type='float',
                      help='learning rate (default: 1e-3)')
    parser.add_option('--sf', '--save-freq', dest='save_freq', default=1, type='int',
                      help='saving frequency of .ckpt models (default: 1)')
    parser.add_option('--sd', '--save-dir', dest='save_dir', default='./models',
                      help='saving directory of .ckpt models (default: ./models)')
    parser.add_option('--init', '--initial-training', dest='initial_training', default=1, type='int',
                      help='train from 1-beginning or 0-resume training (default: 1)')
    parser.add_option('--model', '--model-training', dest='model_training', default='wsdan',
                      help='it can be wsdan,resnet50,resnet100,inception')

    (options, args) = parser.parse_args()

    logging.basicConfig(format='%(asctime)s: %(levelname)s: [%(filename)s:%(lineno)d]: %(message)s', level=logging.INFO)
    warnings.filterwarnings("ignore")

    ##################################
    # Initialize model
    ##################################
    image_size = (400, 400)
    num_classes = 4
    num_attentions = 8
    start_epoch = 0

    if options.model_training == 'inception':
        net = inception_v3(pretrained=True)
        #....


    elif options.model_training == 'resnet50':
        net = resnet50(pretrained=True)
        num_ftrs = net.fc.in_features
        net.fc = nn.Linear(num_ftrs, num_classes)
        options.save_dir = os.path.join(options.save_dir,options.model_training )
        feature_center=None

    elif options.model_training == 'resnet100':
        net = resnet100(pretrained=True)
        num_ftrs = net.fc.in_features
        net.fc = nn.Linear(num_ftrs, num_classes)
        options.save_dir = os.path.join(options.save_dir,options.model_training )
        feature_center=None


     
    #....
    if options.ckpt:
        ckpt = options.ckpt

        if options.initial_training == 0:
            # Get Name (epoch)
            epoch_name = (ckpt.split('/')[-1]).split('.')[0]
            start_epoch = int(epoch_name)

        # Load ckpt and get state_dict
        checkpoint = torch.load(ckpt)
        state_dict = checkpoint['state_dict']

        # Load weights
        net.load_state_dict(state_dict)
        logging.info('Network loaded from {}'.format(options.ckpt))

        
    save_dir = options.save_dir
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    
    cudnn.benchmark = True
    net.to(torch.device("cuda"))
    net = nn.DataParallel(net)
    # Load dataset

    transform = transforms.Compose([transforms.Resize(size=image_size),
                                    transforms.ToTensor(),
                                    transforms.Normalize(mean=[0.485, 0.456, 0.406], 
                                    std=[0.229, 0.224, 0.225])])
    train_dataset = CustomDataset(data_root='/mnt/HDD/RFW/train/data/',csv_file='data/RFW_Train40k_Images_Metada.csv',transform=transform)
    val_dataset = CustomDataset(data_root='/mnt/HDD/RFW/train/data/',csv_file='data/RFW_Val4k_Images_Metadata.csv',transform=transform)
    test_dataset = CustomDataset(data_root='/mnt/HDD/RFW/test/data/',csv_file='data/RFW_Test_Images_Metadata.csv',transform=transform)

    train_loader = DataLoader(train_dataset, batch_size=options.batch_size, shuffle=True,num_workers=options.workers, pin_memory=True)
    validate_loader = DataLoader(val_dataset, batch_size=options.batch_size * 4, shuffle=False,num_workers=options.workers, pin_memory=True)
    test_loader = DataLoader(test_dataset, batch_size=options.batch_size * 4, shuffle=False,num_workers=options.workers, pin_memory=True)

    optimizer = torch.optim.SGD(net.parameters(), lr=options.lr, momentum=0.9, weight_decay=0.00001)
    loss = nn.CrossEntropyLoss()

    
    # Learning rate scheduling
    
    # scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, factor=0.5, patience=2)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=2, gamma=0.9)

    logging.info('')
    logging.info('Start training: Total epochs: {}, Batch size: {}, Training size: {}, Validation size: {}'.
                 format(options.epochs, options.batch_size, len(train_dataset), len(val_dataset)))

    for epoch in range(start_epoch, options.epochs):
        train(options=options,
            epoch=epoch,
              data_loader=train_loader,
              net=net,
              loss=loss,
              optimizer=optimizer,
              save_freq=options.save_freq,
              save_dir=options.save_dir,
              verbose=options.verbose)
        val_loss = validate(data_loader=validate_loader,
                            net=net,
                            loss=loss,
                            verbose=options.verbose,epoch=epoch)
        scheduler.step()


def train(**kwargs):
    # Retrieve training configuration
    options = kwargs['options']
    data_loader = kwargs['data_loader']
    net = kwargs['net']
    loss = kwargs['loss']
    optimizer = kwargs['optimizer']
    epoch = kwargs['epoch']
    save_freq = kwargs['save_freq']
    save_dir = kwargs['save_dir']
    verbose = kwargs['verbose']


    # Default Parameters
    beta = 1e-4
    theta_c = 0.5
    theta_d = 0.5
  

    # metrics initialization
    batches = 0
    epoch_loss = np.array([0, 0, 0], dtype=float)  # Loss on Raw/Crop/Drop Images
    epoch_acc = np.array([[0, 0, 0],
                          [0, 0, 0],
                          [0, 0, 0]], dtype=float)  # Top-1/3/5 Accuracy for Raw/Crop/Drop Images

    # begin training
    start_time = time.time()
    logging.info('Epoch %03d, Learning Rate %g' % (epoch + 1, optimizer.param_groups[0]['lr']))
    net.train()
    for i, (X, y) in enumerate(data_loader):
        batch_start = time.time()
        # obtain data for training
        X = X.to(torch.device("cuda"))
        y = y.to(torch.device("cuda"))

        y_pred = net(X)
        batch_loss = loss(y_pred, y)
        # loss
        epoch_loss[0] += batch_loss.item()
        # backward
        optimizer.zero_grad()
        batch_loss.backward()
        optimizer.step()

        # metrics: top-1, top-3, top-5 error
        with torch.no_grad():
            
            #print(epoch_acc[0][0],acc[0].numpy()[0])
            if epoch == 0:
                epoch_acc[0]= accuracy(y_pred, y, topk=(1, 2, 3))
            else:
                epoch_acc[0] = epoch_acc[0]+accuracy(y_pred, y, topk=(1, 2, 3))
        # end of this batch
        batches += 1
        batch_end = time.time()

        if (i + 1) % verbose == 0:
            logging.info('\tBatch %d: (Raw) Loss %.4f, Accuracy: (%.2f, %.2f, %.2f),'%(i + 1,epoch_loss[0] / batches, epoch_acc[0, 0] / batches, epoch_acc[0, 1] / batches, epoch_acc[0, 2] / batches))
        

    # save checkpoint model
    if epoch % save_freq == 0:
        state_dict = net.module.state_dict()
        for key in state_dict.keys():
            state_dict[key] = state_dict[key].cpu()
        if options.model_training == 'wsdan':
            feature_center = feature_center.cpu()

        torch.save({
            'epoch': epoch,
            'save_dir': save_dir,
            'state_dict': state_dict,
            'model': net,
            'optimizer': optimizer,
            'feature_center': feature_center},

            os.path.join(save_dir, '%03d.ckpt' % (epoch + 1)))

    # end of this epoch
    end_time = time.time()

    # metrics for average
    epoch_loss /= batches
    epoch_acc /= batches

    # show information for this epoch
    logging.info('Train: (Raw) Loss %.4f, Accuracy: (%.2f, %.2f, %.2f), (Crop) Loss %.4f, Accuracy: (%.2f, %.2f, %.2f), (Drop) Loss %.4f, Accuracy: (%.2f, %.2f, %.2f), Time %3.2f'%
                 (epoch_loss[0], epoch_acc[0, 0], epoch_acc[0, 1], epoch_acc[0, 2],
                  epoch_loss[1], epoch_acc[1, 0], epoch_acc[1, 1], epoch_acc[1, 2],
                  epoch_loss[2], epoch_acc[2, 0], epoch_acc[2, 1], epoch_acc[2, 2],
                  end_time - start_time))


def validate(**kwargs):
    # Retrieve training configuration
    data_loader = kwargs['data_loader']
    net = kwargs['net']
    loss = kwargs['loss']
    verbose = kwargs['verbose']
    epoch = kwargs['epoch']

    # Default Parameters
    theta_c = 0.5
    crop_size = (256, 256)  # size of cropped images for 'See Better'

    # metrics initialization
    batches = 0
    epoch_loss = 0
    epoch_acc = np.array([0, 0, 0], dtype='float') # top - 1, 2, 3

    # begin validation
    start_time = time.time()
    net.eval()
    with torch.no_grad():
        for i, (X, y) in enumerate(data_loader):
            batch_start = time.time()

            # obtain data
            X = X.to(torch.device("cuda"))
            y = y.to(torch.device("cuda"))

            y_pred_raw = net(X)

            # loss
            batch_loss = loss(y_pred, y)
            epoch_loss += batch_loss.item()
            if epoch == 0:
                epoch_acc = accuracy(y_pred, y, topk=(1, 2, 3))
            else:
            # metrics: top-1, top-3, top-5 error
                epoch_acc = epoch_acc+ accuracy(y_pred, y, topk=(1, 2, 3))

            # end of this batch
            batches += 1
            batch_end = time.time()
            if (i + 1) % verbose == 0:
                logging.info('\tBatch %d: Loss %.5f, Accuracy: Top-1 %.2f, Top-3 %.2f, Top-5 %.2f, Time %3.2f' %
                         (i + 1, epoch_loss / batches, epoch_acc[0] / batches, epoch_acc[1] / batches, epoch_acc[2] / batches, batch_end - batch_start))


    # end of validation
    end_time = time.time()

    # metrics for average
    epoch_loss /= batches
    epoch_acc /= batches

    # show information for this epoch
    logging.info('Valid: Loss %.5f,  Accuracy: Top-1 %.2f, Top-3 %.2f, Top-5 %.2f, Time %3.2f'%
                 (epoch_loss, epoch_acc[0], epoch_acc[1], epoch_acc[2], end_time - start_time))
    logging.info('')

    return epoch_loss


if __name__ == '__main__':
    main()
