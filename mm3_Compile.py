#!/usr/bin/python
from __future__ import print_function
def warning(*objs):
    print(time.strftime("%H:%M:%S Warning:", time.localtime()), *objs, file=sys.stderr)
def information(*objs):
    print(time.strftime("%H:%M:%S", time.localtime()), *objs, file=sys.stdout)

# import modules
import sys
import os
import time
import inspect
import getopt
import yaml
import traceback
# import h5py
import fnmatch
# import struct
import re
import glob
import gevent
import marshal
# import json # used to write data out in human readable format
from pprint import pprint # for human readable file output
try:
    import cPickle as pickle
except:
    import pickle
from sys import platform as platform
import multiprocessing
from multiprocessing import Pool #, Manager, Lock
import numpy as np
import numpy.ma as ma
from scipy import ndimage

# user modules
# realpath() will make your script run, even if you symlink it
cmd_folder = os.path.realpath(os.path.abspath(
                              os.path.split(inspect.getfile(inspect.currentframe()))[0]))
if cmd_folder not in sys.path:
    sys.path.insert(0, cmd_folder)

# This makes python look for modules in ./external_lib
cmd_subfolder = os.path.realpath(os.path.abspath(
                                 os.path.join(os.path.split(inspect.getfile(
                                 inspect.currentframe()))[0], "external_lib")))
if cmd_subfolder not in sys.path:
    sys.path.insert(0, cmd_subfolder)

import tifffile as tiff
# from subtraction_helpers import *
import mm3_helpers as mm3

# get params is the major function which processes raw TIFF images
def get_tif_params(image_filename, find_channels=True):
    '''This is a damn important function for getting the information
    out of an image. It loads a tiff file, pulls out the image data, and the metadata,
    including the location of the channels if flagged.

    it returns a dictionary like this for each image:

    'filename': image_filename,
    'fov' : image_metadata['fov'], # fov id
    't' : image_metadata['t'], # time point
    'jdn' : image_metadata['jdn'], # absolute julian time
    'x' : image_metadata['x'], # x position on stage [um]
    'y' : image_metadata['y'], # y position on stage [um]
    'plane_names' : image_metadata['plane_names'] # list of plane names
    'channels': cp_dict, # dictionary of channel locations

    Called by
    mm3_Compile.py __main__

    Calls
    mm3.extract_metadata
    mm3.find_channels
    '''

    try:
        # open up file and get metadata
        with tiff.TiffFile(TIFF_dir + image_filename) as tif:
            image_data = tif.asarray()
            image_metadata = mm3.get_tif_metadata_elements(tif)

        # look for channels if flagged
        if find_channels:
            # fix the image orientation and get the number of planes
            image_data = mm3.fix_orientation(image_data)

            # if the image data has more than 1 plane restrict image_data to phase,
            # which should have highest mean pixel data
            if len(image_data.shape) > 2:
                ph_index = np.argmax([np.mean(image_data[ci]) for ci in range(image_data.shape[0])])
                image_data = image_data[ph_index]

            # get shape of single plane
            img_shape = [image_data.shape[0], image_data.shape[1]]

            # find channels on the processed image
            chnl_loc_dict = mm3.find_channel_locs(image_data)

        information('Analyzed %s' % image_filename)

        # return the file name, the data for the channels in that image, and the metadata
        return {'filepath': TIFF_dir + image_filename,
                'fov' : image_metadata['fov'], # fov id
                't' : image_metadata['t'], # time point
                'jd' : image_metadata['jd'], # absolute julian time
                'x' : image_metadata['x'], # x position on stage [um]
                'y' : image_metadata['y'], # y position on stage [um]
                'planes' : image_metadata['planes'], # list of plane names
                'shape' : img_shape, # image shape x y in pixels
                'channels' : chnl_loc_dict} # dictionary of channel locations

    except:
        warning('Failed get_params for ' + image_filename.split("/")[-1])
        print(sys.exc_info()[0])
        print(sys.exc_info()[1])
        print(traceback.print_tb(sys.exc_info()[2]))
        return {'filepath': TIFF_dir + image_filename, 'analyze_success': False}

# slice_and_write cuts up the image files and writes them out to tiff stacks
def tiff_slice_and_write(image_params, channel_masks):
    '''Writes out 4D stacks of TIFF images per channel.


    Called by
    __main__

    Calls

    '''

    information("Writing %s" % image_params['filepath'])

    # load the tif
    with tiff.TiffFile(image_params['filepath']) as tif:
        image_data = tif.asarray()

    # declare identification variables
    fov_id = image_params['fov']
    t_point = image_params['t']

    # fix orientation channels were found in fixed images
    image_data = mm3.fix_orientation(image_data)

    # add additional axis if the image is flat
    if len(image_data.shape) == 2:
        image_data = np.expand_dims(image_data, 0)

    # cut out the channels as per channel masks for this fov
    for peak, channel_loc in channel_masks[image_params['fov']].iteritems():

        channel_slice = mm3.cut_slice(image_data, channel_loc)

        # make a filename for the channel
        # chnl_dir and p will be looked for in the scope above (__main__)
        channel_filename = chnl_dir + p['experiment_name'] + '_xy%03d_p%04d_t%04d.tif' % (fov_id, peak, t_point)

        # Save channel
        tiff.imsave(channel_filename, channel_slice)

        information("Wrote %s" % channel_filename.split('/'[-1]))

    return

# when using this script as a function and not as a library the following will execute
if __name__ == "__main__":
    # hardcoded parameters
    load_metadata = False
    load_channel_masks = False


    # get switches and parameters
    try:
        opts, args = getopt.getopt(sys.argv[1:],"f:")
    except getopt.GetoptError:
        print('No arguments detected (-f).')

    # set parameters
    for opt, arg in opts:
        if opt == '-f':
            param_file_path = arg # parameter file path

    # Load the project parameters file
    # if the paramfile string has no length ie it has not been specified, ERROR
    if len(param_file_path) == 0:
        raise ValueError("a parameter file must be specified (-f <filename>).")
    information ('Loading experiment parameters...')
    with open(param_file_path, 'r') as param_file:
        p = yaml.safe_load(param_file) # load parameters into dictionary

    mm3.init_mm3_helpers(param_file_path) # initialized the helper library

    ### multiprocessing variables and set up.
    # set up a dictionary of locks to prevent HDF5 disk collisions
    # global hdf5_locks
    # hdf5_locks = {x: Lock() for x in range(p['num_fovs'])} # num_fovs is global parameter

    # set up how to manage cores for multiprocessing
    cpu_count = multiprocessing.cpu_count()
    if cpu_count == 32:
        num_analyzers = 20
    elif cpu_count == 8:
        num_analyzers = 14
    else:
        num_analyzers = cpu_count*2 - 2

    # assign shorthand directory names
    TIFF_dir = p['experiment_directory'] + p['image_directory'] # source of images
    ana_dir = p['experiment_directory'] + p['analysis_directory']
    chnl_dir = p['experiment_directory'] + p['analysis_directory'] + 'channels/'

    # create the analysis folder if it doesn't exist
    if not os.path.exists(ana_dir):
        os.makedirs(ana_dir)
    # create folder for sliced data.
    if not os.path.exists(chnl_dir):
        os.makedirs(chnl_dir)

    # declare information variables
    analyzed_imgs = {} # for storing get_params pool results.
    written_imgs = {} # for storing write objects set to write. Are removed once written

    ### process TIFFs for metadata #################################################################
    if load_metadata:
        information("Loading image parameters dictionary.")

        with open(ana_dir + '/TIFF_metadata.pkl', 'r') as tiff_metadata:
            analyzed_images = pickle.load(tiff_metadata)

    else:
        information("Finding image parameters")

        # get all the TIFFs in the folder
        found_files = glob.glob(TIFF_dir + '*.tif') # get all tiffs
        found_files = [filepath.split('/')[-1] for filepath in found_files] # remove pre-path
        found_files = sorted(found_files) # should sort by timepoint

        # get information for all these starting tiffs
        if len(found_files) > 0:
            information("Found %d image files." % len(found_files))
        else:
            warning('No TIFF files found')

        # initialize pool for analyzing image metadata
        pool = Pool(num_analyzers)

        # loop over images and get information
        for fn in found_files:
            # get_params gets the image metadata and puts it in analyzed_imgs dictionary
            # for each file name. True means look for channels

            # This is the non-parallelized version (useful for debug)
            # analyzed_imgs[fn] = get_tif_params(fn, True)

            # Parallelized
            analyzed_imgs[fn] = pool.apply_async(get_tif_params, args=(fn, True))

        information('Waiting for image analysis pool to be finished.')

        pool.close() # tells the process nothing more will be added.
        pool.join() # blocks script until everything has been processed and workers exit

        information('Image analyses pool finished, getting results.')

        # get results from the pool and put them in a dictionary
        for fn, result in analyzed_imgs.iteritems():
            if result.successful():
                analyzed_imgs[fn] = result.get() # put the metadata in the dict if it's good
            else:
                analyzed_imgs[fn] = False # put a false there if it's bad

        information('Got results from analyzed images.')

        # save metadata to a .pkl and a human readable txt file
        with open(ana_dir + '/TIFF_metadata.pkl', 'w') as tiff_metadata:
            pickle.dump(analyzed_imgs, tiff_metadata)
        with open(ana_dir + '/TIFF_metadata.txt', 'w') as tiff_metadata:
            pprint(analyzed_imgs, stream=tiff_metadata)

        information('Saved metadata from analyzed images.')

    ### Make consensus channel masks and get other shared metadata #################################
    if load_channel_masks:
        information("Loading channel masks dictionary.")

        with open(ana_dir + '/channel_masks.pkl', 'w') as cmask_file:
            channel_masks = pickle.load(channel_masks)

    else:
        information("Calculating channel masks.")

        # Uses channel information from the already processed image data
        channel_masks = mm3.make_masks(analyzed_imgs)

        #save the channel mask dictionary to a pickle and a text file
        with open(ana_dir + '/channel_masks.pkl', 'w') as cmask_file:
            pickle.dump(channel_masks, cmask_file)
        with open(ana_dir + '/channel_masks.txt', 'w') as cmask_file:
            pprint(channel_masks, stream=cmask_file)

        information("Channel masks saved.")

    ### Slice and write TIFF files into channels ###################################################
    # try:
        # send writes to the pool based on lowest jdn not written
    # initialize pool for writing images
    pool = Pool(num_analyzers)

    for fov, peaks in channel_masks.iteritems():
        # get filenames just for this fov along with the julian date of acquistion
        send_to_write = [[k, v['jd']] for k, v in analyzed_imgs.items() if v['fov'] == fov]

        # sort the filenames by jdn
        send_to_write = sorted(send_to_write, key=lambda time: time[1])

        # initialize pool for writing images
        pool = Pool(num_analyzers)

        # writing out each time point
        for fn, jd in send_to_write:
            # get the image parameter dictionary from the analyzed image dict.
            image_params = analyzed_imgs[fn]

            # send to function which slices and writes channels out
            #tiff_slice_and_write(image_params, channel_masks)

            written_imgs[fn] = pool.apply_async(tiff_slice_and_write,
                                                args=(image_params, channel_masks))

    information('Waiting for channel write pool to be finished.')

    pool.close() # tells the process nothing more will be added.
    pool.join() # blocks script until everything has been processed and workers exit

    information('Channel write pool finished.')

            # # data_writer is the major hdf5 writing function.
            # # not switched for saving originals and doing subtraction
            # w_result_dict[fov_fns_jdns[0][0]] = wpool.apply_async(data_writer,
            #                             [image_metadata[fov_fns_jdns[0][0]],
            #                             channel_masks, subtract_on_datawrite[image_metadata[fov_fns_jdns[0][0]]['fov']],
            #                             save_originals])
            # image_metadata[fov_fns_jdns[0][0]]['sent_to_write'] = True

    # except:
    #     warning("Channel slicing try block failed.")
    #     print(sys.exc_info()[0])
    #     print(sys.exc_info()[1])
    #     print(traceback.print_tb(sys.exc_info()[2]))
