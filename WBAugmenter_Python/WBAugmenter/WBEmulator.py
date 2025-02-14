################################################################################
# Copyright (c) 2019-present, Mahmoud Afifi
#
# This source code is licensed under the license found in the LICENSE file in
# the root directory of this source tree.
#
# Please, cite the following paper if you use this code:
# Mahmoud Afifi and Michael S. Brown. What else can fool deep learning?
# Addressing color constancy errors on deep neural network performance. ICCV,
# 2019
#
# Email: mafifi@eecs.yorku.ca | m.3afifi@gmail.com
################################################################################


import numpy as np
import numpy.matlib
import cv2
import random as rnd
import os
import shutil


class WBEmulator:
  def __init__(self):
    # training encoded features
    self.features = np.load('params/features.npy')
    # mapping functions to emulate WB effects
    self.mappingFuncs = np.load('params/mappingFuncs.npy')
    # weight matrix for histogram encoding
    self.encoderWeights = np.load('params/encoderWeights.npy')
    # bias vector for histogram encoding
    self.encoderBias = np.load('params/encoderBias.npy')
    self.h = 60  # histogram bin width
    self.K = 25  # K value for nearest neighbor searching
    self.sigma = 0.25  # fall off factor for KNN
    # WB & photo finishing styles
    self.wb_photo_finishing = ['_F_AS', '_F_CS', '_S_AS', '_S_CS',
                               '_T_AS', '_T_CS', '_C_AS', '_C_CS',
                               '_D_AS', '_D_CS']

  def encode(self, hist):
    """Generates a compacted feature of a given RGB-uv histogram tensor."""
    histR_reshaped = np.reshape(np.transpose(hist[:, :, 0]),
                                (1, int(hist.size / 3)), order="F")
    histG_reshaped = np.reshape(np.transpose(hist[:, :, 1]),
                                (1, int(hist.size / 3)), order="F")
    histB_reshaped = np.reshape(np.transpose(hist[:, :, 2]),
                                (1, int(hist.size / 3)), order="F")
    hist_reshaped = np.append(histR_reshaped,
                              [histG_reshaped, histB_reshaped])
    feature = np.dot(hist_reshaped - self.encoderBias.transpose(),
                     self.encoderWeights)
    return feature

  def rgbuv_hist(self, I):
    """Computes an RGB-uv histogram tensor."""
    sz = np.shape(I)  # get size of current image
    if sz[0] * sz[1] > 202500:  # resize if it is larger than 450*450
      factor = np.sqrt(202500 / (sz[0] * sz[1]))  # rescale factor
      newH = int(np.floor(sz[0] * factor))
      newW = int(np.floor(sz[1] * factor))
      I = cv2.resize(I, (newW, newH), interpolation=cv2.INTER_NEAREST)
    I_reshaped = I[(I > 0).all(axis=2)]
    eps = 6.4 / self.h
    A = np.arange(-3.2, 3.19, eps)  # dummy vector
    hist = np.zeros((A.size, A.size, 3))  # histogram will be stored here
    Iy = np.linalg.norm(I_reshaped, axis=1)  # intensity vector
    for i in range(3):  # for each histogram layer, do
      r = []  # excluded channels will be stored here
      for j in range(3):  # for each color channel do
        if j != i:  # if current channel does not match current layer,
          r.append(j)  # exclude it
      Iu = np.log(I_reshaped[:, i] / I_reshaped[:, r[1]])
      Iv = np.log(I_reshaped[:, i] / I_reshaped[:, r[0]])
      hist[:, :, i], _, _ = np.histogram2d(
        Iu, Iv, bins=self.h, range=((-3.2 - eps / 2, 3.2 - eps / 2),) * 2,
        weights=Iy)
      norm_ = hist[:, :, i].sum()
      hist[:, :, i] = np.sqrt(hist[:, :, i] / norm_)  # (hist/norm)^(1/2)
    return hist

  def generateWbsRGB(self, I, outNum=10):
    """Generates outNum new images of a given image I."""
    assert (outNum <= 10)
    I = cv2.cvtColor(I, cv2.COLOR_BGR2RGB)  # convert from BGR to RGB
    I = im2double(I)  # convert to double
    feature = self.encode(self.rgbuv_hist(I))
    if outNum < len(self.wb_photo_finishing):
      wb_pf = rnd.sample(self.wb_photo_finishing, outNum)
      inds = []
      for j in range(outNum):
        inds.append(self.wb_photo_finishing.index(wb_pf[j]))

    else:
      wb_pf = self.wb_photo_finishing
      inds = list(range(0, len(wb_pf)))
    synthWBimages = np.zeros((I.shape[0], I.shape[1],
                              I.shape[2], len(wb_pf)))

    D_sq = np.einsum('ij, ij ->i', self.features,
                     self.features)[:, None] + np.einsum(
      'ij, ij ->i', feature, feature) - 2 * self.features.dot(feature.T)

    # get smallest K distances
    idH = D_sq.argpartition(self.K, axis=0)[:self.K]
    dH = np.sqrt(
      np.take_along_axis(D_sq, idH, axis=0))
    weightsH = np.exp(-(np.power(dH, 2)) /
                      (2 * np.power(self.sigma, 2)))  # compute weights
    weightsH = weightsH / sum(weightsH)  # normalize blending weights
    for i in range(len(inds)):  # for each of the retried training examples,
      ind = inds[i]  # for each WB & PF style,
      # generate a mapping function
      mf = sum(np.reshape(np.matlib.repmat(weightsH, 1, 27),
                          (self.K, 1, 9, 3)) *
               self.mappingFuncs[(idH - 1) * 10 + ind, :])
      mf = mf.reshape(9, 3, order="F")  # reshape it to be 9 * 3
      synthWBimages[:, :, :, i] = changeWB(I, mf)  # apply it!
    return synthWBimages, wb_pf

  def single_image_processing(self, in_img, out_dir="../results", outNum=10,
                              write_original=1):
    """Applies the WB emulator to a single image in_img."""
    assert (outNum <= 10)
    print("processing image: " + in_img + "\n")
    filename, file_extension = os.path.splitext(in_img)  # get file parts
    I = cv2.imread(in_img)  # read the image
    # generate new images with different WB settings
    outImgs, wb_pf = self.generateWbsRGB(I, outNum)
    for i in range(outNum):  # save images
      outImg = outImgs[:, :, :, i]  # get the ith output image
      cv2.imwrite(out_dir + '/' + os.path.basename(filename) +
                  wb_pf[i] + file_extension, outImg * 255)  # save it
      if write_original == 1:
        cv2.imwrite(out_dir + '/' + os.path.basename(filename) +
                    '_original' + file_extension, I)

  def batch_processing(self, in_dir, out_dir="../results", outNum=10,
                       write_original=1):
    """Applies the WB emulator to all images in a given directory in_dir."""
    assert (outNum <= 10)
    imgfiles = []
    valid_images = (".jpg", ".bmp", ".png", ".tga")
    for f in os.listdir(in_dir):
      if f.lower().endswith(valid_images):
        imgfiles.append(os.path.join(in_dir, f))
    for in_img in imgfiles:
      print("processing image: " + in_img + "\n")
      filename, file_extension = os.path.splitext(in_img)
      I = cv2.imread(in_img)
      outImgs, wb_pf = self.generateWbsRGB(I, outNum)
      for i in range(outNum):  # save images
        outImg = outImgs[:, :, :, i]  # get the ith output image
        cv2.imwrite(out_dir + '/' + os.path.basename(filename) +
                    wb_pf[i] + file_extension, outImg * 255)  # save it
        if write_original == 1:
          cv2.imwrite(out_dir + '/' + os.path.basename(filename) +
                      '_original' + file_extension, I)

  def trainingGT_processing(self, in_dir, out_dir, gt_dir, out_gt_dir, gt_ext,
                            outNum=10, write_original=1):
    """Applies the WB emulator to all training images in in_dir and
        generates corresponding GT files"""
    imgfiles = []  # image files will be saved here
    gtfiles = []  # ground truth files will be saved here
    # valid image file extensions (modify it if needed)
    valid_images = (".jpg", ".bmp", ".png", ".tga")
    for f in os.listdir(in_dir):  # for each file in in_dir
      if f.lower().endswith(valid_images):
        imgfiles.append(os.path.join(in_dir, f))

    # get corresponding ground truth files
    for in_img in imgfiles:
      filename, file_extension = os.path.splitext(in_img)
      gtfiles.append(os.path.join(gt_dir, os.path.basename(filename) +
                                  gt_ext))

    for in_img, gtfile in zip(imgfiles, gtfiles):
      print("processing image: " + in_img + "\n")
      filename, file_extension = os.path.splitext(in_img)
      gtbasename, gt_extension = os.path.splitext(gtfile)
      gtbasename = os.path.basename(gtbasename)
      I = cv2.imread(in_img)
      # generate new images with different WB settings
      outImgs, wb_pf = self.generateWbsRGB(I, outNum)
      for i in range(outNum):
        outImg = outImgs[:, :, :, i]
        cv2.imwrite(out_dir + '/' + os.path.basename(filename) +
                    wb_pf[i] + file_extension, outImg * 255)  # save it
        shutil.copyfile(gtfile,  # copy corresponding gt file
                        os.path.join(out_gt_dir, gtbasename + wb_pf[i] +
                                     gt_extension))

        if write_original == 1:  # if write_original flag is true
          cv2.imwrite(out_dir + '/' + os.path.basename(filename) +
                      '_original' + file_extension, I)
          # copy corresponding gt file
          shutil.copyfile(gtfile, os.path.join(
            out_gt_dir, gtbasename + '_original' + gt_extension))


def changeWB(input, m):
  """Applies a mapping function m to a given input image."""
  sz = np.shape(input)  # get size of input image
  I_reshaped = np.reshape(input, (int(input.size / 3), 3),
                          order="F")
  kernel_out = kernelP9(I_reshaped)  # raise input image to a higher-dim space
  # apply m to the input image after raising it the selected higher degree
  out = np.dot(kernel_out, m)
  out = outOfGamutClipping(out)  # clip out-of-gamut pixels
  # reshape output image back to the original image shape
  out = out.reshape(sz[0], sz[1], sz[2], order="F")
  out = cv2.cvtColor(out.astype('float32'), cv2.COLOR_RGB2BGR)
  return out


def kernelP9(I):
  """Kernel function: kernel(r, g, b) -> (r, g, b, r2, g2, b2, rg, rb, gb)"""
  return (np.transpose((I[:, 0], I[:, 1], I[:, 2], I[:, 0] * I[:, 0],
                        I[:, 1] * I[:, 1], I[:, 2] * I[:, 2], I[:, 0] * I[:, 1],
                        I[:, 0] * I[:, 2], I[:, 1] * I[:, 2])))


def outOfGamutClipping(I):
  """Clips out-of-gamut pixels."""
  I[I > 1] = 1  # any pixel is higher than 1, clip it to 1
  I[I < 0] = 0  # any pixel is below 0, clip it to 0
  return I


def im2double(im):
  """Returns a double image [0,1] of the uint8 im [0,255]."""
  return cv2.normalize(im.astype('float'), None, 0.0, 1.0, cv2.NORM_MINMAX)
