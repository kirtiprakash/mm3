import os,sys,glob
import cPickle as pkl
import argparse
import yaml
import numpy as np
import time
import shutil
import scipy.io as spio
import re
import subprocess as sp
from freetype import *
import inspect
import warnings
from PIL import Image

# This makes python look for modules in ./external_lib
cmd_subfolder = os.path.realpath(os.path.abspath(
                                 os.path.join(os.path.split(inspect.getfile(
                                 inspect.currentframe()))[0], "external_lib")))
if cmd_subfolder not in sys.path:
    sys.path.insert(0, cmd_subfolder)
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import tifffile as tiff

import mm3_helpers
from mm3_helpers import get_fov, get_time, information
from mm3_utils import print_time, make_label, array_bin, get_background, plot_histogram

# yaml formats
npfloat_representer = lambda dumper,value: dumper.represent_float(float(value))
nparray_representer = lambda dumper,value: dumper.represent_list(value.tolist())
float_representer = lambda dumper,value: dumper.represent_scalar(u'tag:yaml.org,2002:float', "{:<.6g}".format(value))
yaml.add_representer(float,float_representer)
yaml.add_representer(np.float_,npfloat_representer)
yaml.add_representer(np.ndarray,nparray_representer)

################################################
# main
################################################
if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="Making movie from .tif files.")
    parser.add_argument('-f', '--paramfile',  type=file, required=True, help='Yaml file containing parameters.')
    parser.add_argument('-o', '--fov',  type=int, required=True, help='Field of view with which make the movie.')
    parser.add_argument('--debug',  action='store_true', required=False, help='Debug mode.')
    parser.add_argument('--histograms',  type=int, nargs='+', required=False, help='Debug mode.')
    namespace = parser.parse_args(sys.argv[1:])
    paramfile = namespace.paramfile.name
    allparams = yaml.load(namespace.paramfile)
    fov = namespace.fov
    debug=namespace.debug
    if debug:
        debugdir = 'debug'
        if not os.path.isdir(debugdir):
            os.makedirs(debugdir)

    if not (namespace.histograms is None):
        hist_list = namespace.histograms
        histdir='histograms'
        if not os.path.isdir(histdir):
            os.makedirs(histdir)
    else:
        hist_list = []

    # first initialization of parameters
    params = allparams['movie']

################################################
# make movie directory
################################################
    print print_time(), "Making directory..."
    movie_dir = params['directory']

    if not movie_dir.startswith('/'):
        movie_dir = os.path.join('.', movie_dir)
    if not os.path.isdir(movie_dir):
        os.makedirs(movie_dir)

################################################
# make list of images
################################################
    exp_name = allparams['experiment_name']
    tiffs = allparams['image_directory']
    pattern = exp_name + '_t(\d+)xy\w+.tif'
    filelist = []
    for root, dirs, files in os.walk(tiffs):
        for f in files:
            res = re.match(pattern, f)
            if not (res is None):
                # determine fov
                if (fov == get_fov(f)):
                    filelist.append(os.path.join(root,f))

        # do not go beyond first level
        break

    if (len(filelist) == 0):
        sys.exit("File list is empty!")

    # open one image to get dimensions
    fimg=filelist[0]
    img = tiff.imread(fimg) # read the image
    if (len(img.shape) == 2):
        img = np.array([img])
    if (len(img.shape) != 3):
        sys.exit('wrong image format/dimensions!')
    img = np.moveaxis(img, 0, 2)
    size_y_ref, size_x_ref = img.shape[:2]
    # make sure the output image has dimensions multiple of 2
    # in addition, ffmpeg will issue a warning of 'data is not aligned' if dimensions
    # are not multiple of 8, 16 or 32
    # so let's choose 8 as basis instead.
    size_y_ref = (size_y_ref / 8) * 8
    size_x_ref = (size_x_ref / 8) * 8

################################################
# make movie
################################################
    # set command to give to ffmpeg
    # path to FFMPEG
    FFMPEG_BIN = sp.check_output("which ffmpeg", shell=True).replace('\n','')

    # path to font for label
    fontfile = "/Library/Fonts/Andale Mono.ttf"    # Mac OS location
    if not os.path.isfile(fontfile):
        # fontfile = "/usr/share/fonts/truetype/freefont/FreeMono.ttf"  # Linux Ubuntu 16.04 location
        fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf" # Another font
        if not os.path.isfile(fontfile):
            sys.exit("You need to install some fonts and specify the correct path to the .ttf file!")
    fontface = Face(fontfile)

    # ffmpeg command
    # 'ffmpeg -f image2 -pix_fmt gray16le -r 2 -i test/16b/20180223_SEM4158_col1_mopsgluc_dnp_t%04dxy01.tif -an -c:v h264 -pix_fmt yuv420p movies/test.mp4'
    command = [FFMPEG_BIN,
            '-y', # (optional) overwrite output file if it exists
            '-f', 'rawvideo',
            '-c:v','rawvideo',
            '-s', '%dx%d' % (size_x_ref, size_y_ref), # size of one frame
            '-pix_fmt', 'rgb24',
            '-r', '%d' % params['fps'], # frames per second
            '-i', '-', # The imput comes from a pipe
            '-an', # Tells FFMPEG not to expect any audio
            # options for the h264 codec
            '-c:v', 'h264',
            '-pix_fmt', 'yuv420p',

            # set the movie name
            os.path.join(movie_dir,allparams['experiment_name']+'_xy%03d.mp4' % fov)]

    information('Writing movie for FOV %d.' % fov)

    #print " ".join(command)
    if not debug:
        pipe = sp.Popen(command, stdin=sp.PIPE)

    # data dictionary for histogram
    hist_data = {}
    try:
        for i in hist_list:
            hist_data[i]=[]
    except KeyError:
        pass

    img_old=None
    for fimg in filelist:
        t = get_time(fimg)
        if not (params['t0'] is None) and (t < params['t0']):
            continue
        if not (params['tN'] is None) and (t > params['tN']):
            continue

        if debug:
            print "t = {:d}".format(t)

        # read the image
        img = tiff.imread(fimg)

        # standardize dimension and ensure the axis order y,x,channel
        if (len(img.shape) == 2):
            img = np.array([img])
        if (len(img.shape) != 3):
            sys.exit('wrong image format/dimesions!')
        img = np.moveaxis(img, 0, 2)
        size_y, size_x = img.shape[:2]
        if not (size_y == size_y_ref and size_x == size_x_ref):
            img = img[:size_y_ref, :size_x_ref]
        size_y, size_x = img.shape[:2]
        nchannels = img.shape[2]

        # compare with old image and use previous data if some channel are empty
        # happens when the sampling frequency of the fluorescence channel is smaller than the phase
        # contrast one
        if not (img_old is None):
            for i in range(nchannels):
                if (np.sum(img[:,:,i]) == 0):
                    img[:,:,i] = img_old[:,:,i]
                else:
                    img_old[:,:,i]=img[:,:,i]
        else:
            # copy image
            img_old=img

        # start ratiometric
        ## import parameters
        if ('ratiometric' in params) and (not params['ratiometric'] is None):
            try:
                new_channel = bool(params['ratiometric']['new_channel'])
            except (KeyError, TypeError):
                new_channel=False

            cnum=int(params['ratiometric']['channel_num'])
            cden=int(params['ratiometric']['channel_den'])
            cratio=int(params['ratiometric']['channel_new'])
            try:
                bg_sbtract=bool(params['ratiometric']['bg_subtract'])
            except KeyError:
                bg_sbtract=True
            try:
                bin_neighbors=int(params['ratiometric']['bin_neighbors'])
            except (KeyError, TypeError):
                bin_neighbors=0
            try:
                bg_diff=float(params['ratiometric']['bg_diff'])
            except (KeyError, TypeError):
                bg_diff=1.5
            try:
                xlo_num=float(params['ratiometric']['xlo_num'])
            except (KeyError, TypeError):
                xlo_num=0.
            try:
                xlo_den=float(params['ratiometric']['xlo_den'])
            except (KeyError, TypeError):
                xlo_den=0.

            if not ( (cnum in range(nchannels)) and (cden in range(nchannels))):
                raise ValueError("cnum and cden do not correspond to input channels")
            if cratio < nchannels:
                print "WARNING: overriding input channel {} for ratiometric one.".format(cratio)
            elif cratio > nchannels:
                raise ValueError("Ratiometric channel cannot be > # input channels")

            img_num = np.array(img[:,:,cnum], dtype=np.float)
            img_den = np.array(img[:,:,cden], dtype=np.float)

            if not new_channel:
                # just compute fluorescence ratio
                bg_num = get_background(np.ravel(img_num),bg_diff)
                bg_den = get_background(np.ravel(img_den),bg_diff)
                img_num -= bg_num
                img_den -= bg_den
                img_num [img_num < 0] = 0.
                img_den [img_den < 0] = 0.

                ratiometric_ratio = np.sum(img_num)/np.sum(img_den)

            else:
                # process fluorescence images to build extra channel
                ## get mask before applying operations on array
                idx = (img_num > xlo_num) & (img_den > xlo_den)

                ## binning
                img_num = array_bin(img_num, p=bin_neighbors)
                img_den = array_bin(img_den, p=bin_neighbors)

                ## background subtraction
                bg_num = get_background(np.ravel(img_num),bg_diff)
                bg_den = get_background(np.ravel(img_den),bg_diff)
                img_num -= bg_num
                img_den -= bg_den
                img_num [img_num < 0] = 0.
                img_den [img_den < 0] = 0.


                # debug purpose
                if debug:
                    for img_plot,suff in zip([img_num, img_den],["num", "den"]):
                        img_plot=np.copy(img_plot)
                        img_plot[~idx]=0.
                        img_plot = (np.float_(img_plot) - np.min(img_plot)) / np.float_(np.max(img_plot)-np.min(img_plot))
                        img_plot = np.uint8(img_plot*255)
                        Image.fromarray(img_plot, mode='L').save(os.path.join(debugdir,'test_ratiometric_{}_t{:04d}.png'.format(suff,t)))

                ## create new channel with ratio
                img_ratio = np.zeros(img_num.shape, dtype=np.float)
                idx = idx & (img_num > 0.) & (img_den > 0.)
                img_ratio[idx] = (img_num[idx] / img_den[idx])

                img_ratio = np.moveaxis(np.array([img_ratio]), 0, 2)
                img = np.concatenate((img, img_ratio), axis=2)

                # compute ratio
                ratiometric_ratio = np.sum(img_num[idx])/np.sum(img_den[idx])

            # end ratiometric

        # start channel stack
        stack=[]
        masks={}
        nchannels = img.shape[2]
        for i in range(nchannels):
            if debug:
                print "channel {:d}".format(i)

            img_tp = img[:,:,i]

            # add to histogram if necessary
            if i in hist_data:
                hist_data[i] = np.concatenate((hist_data[i], np.ravel(img_tp)))

            # determine image minmax
            try:
                if params['channels'][i]['min'] == 'background':
                    pmin = get_background(np.ravel(img_tp),1.5)
                else:
                    pmin = float(params['channels'][i]['min'])
            except (KeyError,TypeError):
                pmin = np.min(img_tp)
            params['channels'][i]['min']=pmin

            try:
                pmax = float(params['channels'][i]['max'])
            except (KeyError,TypeError):
                pmax = np.max(img_tp)
            params['channels'][i]['max']=pmax

            # masking operations (used when building overlay)
            mask =  None
            try:
                xlo = float(params['channels'][i]['xlo'])
            except (KeyError, TypeError):
                xlo = pmin

            mask = (img_tp > xlo) # get binary mask
            masks[i] = mask

            # rescale dynamic range
            img_tp = (np.array(img_tp, dtype=np.float_) - pmin)/float(pmax-pmin)
            img_tp [img_tp < 0] = 0.
            img_tp [img_tp > 1] = 1.

            # color
            try:
                color = params['channels'][i]['rgb']
            except KeyError:
                color = [255,255,255]

            #img_tp = (1. - img_tp)
            norm = float(2**8 - 1)
            img_tp *= norm
            #rgba = np.dstack([img_tp*color[0]/255., img_tp*color[1]/255., img_tp*color[2]/255., np.ones(img_tp.shape)*255.])
            #rgba = np.array(rgba,dtype=np.uint8)
            #stack.append(rgba)
            #rgb = np.dstack([img_tp*color[0]/255., img_tp*color[1]/255., img_tp*color[2]/255.])
            rgb = np.dstack([img_tp*color[0]/norm, img_tp*color[1]/norm, img_tp*color[2]/norm])
            rgb = np.array(rgb,dtype=np.uint8)
            stack.append(rgb)

        # construct final image
        bg = params['background']
        try:
            img_bg = stack[bg]
        except IndexError:
            sys.exit("Channel {:d} doesn't exist in input data and cannot be used for background.".format(bg))

        # start overlays
        overlay = []
        try:
            overlay = params['overlay']
        except KeyError:
            pass

        if (not (overlay is None) or (overlay == []) ):
            img = np.zeros(img_bg.shape, dtype=np.float_)
            tot_coeffs = np.zeros(img_bg.shape, dtype=np.float_)
            for i in overlay:
                try:
                    img_tp = stack[i]
                except IndexError:
                    sys.exit("Channel {:d} doesn't exist in input data and cannot be overlaid".format(i))
                w = params['channels'][i]['alpha']
                mask = masks[i]
                coeffs = np.ones(img_tp.shape,dtype=np.float_)
                if not (mask is None):
                    coeffs[~mask] = 0.

                coeffs *= w
                img += coeffs*img_tp.astype('float')
                tot_coeffs += coeffs

            img += (1-tot_coeffs)*img_bg.astype('float')
            img = np.array(img, dtype=np.uint8)

        else:
            img = img_bg
        # end overlays

        # add time stamp
        size_y,size_x = img.shape[:2]
        seconds = float((t-1) * allparams['seconds_per_time_index']) # t=001 is the first capture
        mins = seconds / 60
        hours = mins / 60
        timedata = "%dhrs %02dmin" % (hours, mins % 60)
        r_timestamp = np.fliplr(make_label(timedata, fontface, size=48,
                                           angle=180)).astype('float64')
        r_timestamp = np.pad(r_timestamp, ((size_y - 10 - r_timestamp.shape[0], 10),
                                           (size_x - 10 - r_timestamp.shape[1], 10)),
                                           mode = 'constant')

        mask = (r_timestamp > 0)
        r_timestamp = np.dstack((r_timestamp, r_timestamp, r_timestamp)).astype(np.uint8)
        img[mask] = r_timestamp[mask]

        # add global ratio for ratiometric input
        if ('ratiometric' in params) and (not params['ratiometric'] is None):
            ratiotxt = "ratio = {:.2f}".format(ratiometric_ratio)
            if np.isfinite(ratiometric_ratio):
                ratio_img = np.fliplr(make_label(ratiotxt, fontface, size=48,
                                                   angle=180)).astype('float64')
                ratio_img = np.pad(ratio_img, ((size_y - 10 - ratio_img.shape[0], 10),
                                               (10, size_x - 10 - ratio_img.shape[1])),
                                               mode = 'constant')
                mask = (ratio_img > 0)
                ratio_img = np.dstack((ratio_img, ratio_img, ratio_img)).astype(np.uint8)
                img[mask] = ratio_img[mask]

        # debug purpose
        if debug:
            img = Image.fromarray(img, mode='RGB')
            img.save(os.path.join(debugdir,'test_t{:04d}.png'.format(t)))

        # write the image to the ffmpeg subprocess
        if not debug:
            pipe.stdin.write(img.tostring())

    # end of loop
    if not debug:
        pipe.terminate()

    # histograms
    for i in hist_data.keys():
        filehist = os.path.join(histdir,"histogram_c{:d}.pdf".format(i))
        plot_histogram(hist_data[i], filehist)