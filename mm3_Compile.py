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
import glob
from pprint import pprint # for human readable file output
try:
    import cPickle as pickle
except:
    import pickle
import multiprocessing
from multiprocessing import Pool #, Lock
import numpy as np
import warnings
import h5py

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

# supress the warning this always gives
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import tifffile as tiff

# this is the mm3 module with all the useful functions and classes
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

            if p['TIFF_source'] == 'elements':
                image_metadata = mm3.get_tif_metadata_elements(tif)
            elif p['TIFF_source'] == 'nd2ToTIFF':
                image_metadata = mm3.get_tif_metadata_nd2ToTIFF(tif)

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
    This appends to a stack if it already exists, and is so slow it is not used.

    Called by
    __main__

    Calls

    '''

    information("Writing %s to channels." % image_params['filepath'].split('/')[-1])

    # load the tif
    with tiff.TiffFile(image_params['filepath']) as tif:
        image_data = tif.asarray()

    # declare identification variables
    fov_id = image_params['fov']
    #t_point = image_params['t']

    # fix orientation channels were found in fixed images
    image_data = mm3.fix_orientation(image_data)

    # add additional axis if the image is flat
    if len(image_data.shape) == 2:
        image_data = np.expand_dims(image_data, 0)

    # change axis so it goes X, Y, Plane
    image_data = np.rollaxis(image_data, 0, 3)

    # cut out the channels as per channel masks for this fov
    for peak, channel_loc in channel_masks[image_params['fov']].iteritems():
        # slice out channel
        channel_slice = mm3.cut_slice(image_data, channel_loc)

        # this is the filename for the channel
        # chnl_dir and p will be looked for in the scope above (__main__)
        channel_filename = chnl_dir + p['experiment_name'] + '_xy%03d_p%04d.tif' % (fov_id, peak)

        # check if it alread exists, append to it if so, make it if not
        try:
            with tiff.TiffFile(channel_filename) as channel_stack_file:
                # load it up
                channel_stack = channel_stack_file.asarray()

            # add a dimension for time
            channel_slice = np.expand_dims(channel_slice, axis=0)
            if len(channel_stack.shape) == 3:
                channel_stack = np.expand_dims(channel_stack, axis=0)

            # add on the new channel in the time dimension
            channel_stack = np.concatenate([channel_stack, channel_slice], axis=0)

            # save over the old stack
            tiff.imsave(channel_filename, channel_stack, compress=tif_compress)

        except:
            information('First save for %s' % channel_filename.split('/')[-1])
            # otherwise just save the first slice
            tiff.imsave(channel_filename, channel_slice, compress=tif_compress)

    return

# slice_and_write cuts up the image files one at a time and writes them out to tiff stacks
def tiff_stack_slice_and_write(images_to_write, channel_masks):
    '''Writes out 4D stacks of TIFF images per channel.
    Loads all tiffs from and FOV into memory and then slices all time points at once.

    Called by
    __main__
    '''

    # make an array of images and then concatenate them into one big stack
    image_fov_stack = []

    # go through list of images and get the file path
    for n, image in enumerate(images_to_write):
        # analyzed_imgs dictionary will be found in main scope. [0] is the key, [1] is jd
        image_params = analyzed_imgs[image[0]]

        information("Loading %s." % image_params['filepath'].split('/')[-1])

        if n == 1:
            # declare identification variables for saving using first image
            fov_id = image_params['fov']

        # load the tif and store it in array
        with tiff.TiffFile(image_params['filepath']) as tif:
            image_data = tif.asarray()

        # channel finding was also done on images after orientation was fixed
        image_data = mm3.fix_orientation(image_data)

        # add additional axis if the image is flat
        if len(image_data.shape) == 2:
            image_data = np.expand_dims(image_data, 0)

        #change axis so it goes X, Y, Plane
        image_data = np.rollaxis(image_data, 0, 3)

        # add it to list. The images should be in time order
        image_fov_stack.append(image_data)

    # concatenate the list into one big ass stack
    image_fov_stack = np.stack(image_fov_stack, axis=0)

    # cut out the channels as per channel masks for this fov
    for peak, channel_loc in channel_masks[image_params['fov']].iteritems():
        #information('Slicing and saving channel peak %s.' % channel_filename.split('/')[-1])
        information('Slicing and saving channel peak %d.' % peak)

        # slice out channel.
        # The function should recognize the shape length as 4 and cut all time points
        channel_stack = mm3.cut_slice(image_fov_stack, channel_loc)

        # save a different time stack for all colors
        for color_index in range(channel_stack.shape[3]):
            # this is the filename for the channel
            # # chnl_dir and p will be looked for in the scope above (__main__)
            channel_filename = chnl_dir + p['experiment_name'] + '_xy%03d_p%04d_c%1d.tif' % (fov_id, peak, color_index)
            # save stack
            tiff.imsave(channel_filename, channel_stack[:,:,:,color_index], compress=tif_compress)

    return

# same thing but do it for hdf5
def hdf5_stack_slice_and_write(images_to_write, channels_masks):
    '''Writes out 4D stacks of TIFF images to an HDF5 file.

    Called by
    __main__
    '''

    # make an array of images and then concatenate them into one big stack
    image_fov_stack = []

    # make arrays for filenames and times
    image_filenames = []
    image_times = [] # times is still an integer but may be indexed arbitrarily
    image_jds = [] # jds = julian dates (times)

    # go through list of images, load and fix them, and create arrays of metadata
    for n, image in enumerate(images_to_write):
        image_name = image[0] # [0] is the key, [1] is jd

        # analyzed_imgs dictionary will be found in main scope.
        image_params = analyzed_imgs[image_name]
        information("Loading %s." % image_params['filepath'].split('/')[-1])

        # add information to metadata arrays
        image_filenames.append(image_name)
        image_times.append(image_params['t'])
        image_jds.append(image_params['jd'])

        # declare identification variables for saving using first image
        if n == 1:
            # same across fov
            fov_id = image_params['fov']
            x_loc = image_params['x']
            y_loc = image_params['y']
            image_shape = image_params['shape']
            image_planes = image_params['planes']

        # load the tif and store it in array
        with tiff.TiffFile(image_params['filepath']) as tif:
            image_data = tif.asarray()

        # channel finding was also done on images after orientation was fixed
        image_data = mm3.fix_orientation(image_data)

        # add additional axis if the image is flat
        if len(image_data.shape) == 2:
            image_data = np.expand_dims(image_data, 0)

        #change axis so it goes X, Y, Plane
        image_data = np.rollaxis(image_data, 0, 3)

        # add it to list. The images should be in time order
        image_fov_stack.append(image_data)

    # concatenate the list into one big ass stack
    image_fov_stack = np.stack(image_fov_stack, axis=0)

    # create the HDF5 file for the FOV, first time this is being done.
    with h5py.File(hdf5_dir + 'xy%03d.hdf5' % fov_id, 'w', libver='earliest') as h5f:

        # add in metadata for this FOV
        # these attributes should be common for all channel
        h5f.attrs.create('fov_id', fov_id)
        h5f.attrs.create('stage_x_loc', x_loc)
        h5f.attrs.create('stage_y_loc', y_loc)
        h5f.attrs.create('image_shape', image_shape)
        h5f.attrs.create('planes', image_planes)
        h5f.attrs.create('peaks', sorted(channel_masks[fov_id].keys()))

        # this is for things that change across time, for these create a dataset
        h5ds = h5f.create_dataset(u'filenames', data=image_filenames, maxshape=(None), dtype='S100')
        h5ds = h5f.create_dataset(u'times', data=image_times, maxshape=(None))
        h5ds = h5f.create_dataset(u'times_jd', data=image_jds, maxshape=(None))

        # cut out the channels as per channel masks for this fov
        for peak, channel_loc in channel_masks[fov_id].iteritems():
            #information('Slicing and saving channel peak %s.' % channel_filename.split('/')[-1])
            information('Slicing and saving channel peak %d.' % peak)

            # create group for this channel
            h5g = h5f.create_group('channel_%04d' % peak)

            # add attribute for peak_id, channel location
            h5g.attrs.create('peak_id', peak)
            h5g.attrs.create('channel_loc', channel_loc)

            # slice out channel.
            # The function should recognize the shape length as 4 and cut all time points
            channel_stack = mm3.cut_slice(image_fov_stack, channel_loc)

            # save a different dataset  for all colors
            for color_index in range(channel_stack.shape[3]):

                # create the dataset for the image. Review docs for these options.
                h5ds = h5g.create_dataset(u'p%04d_c%1d' % (peak, color_index),
                                data=channel_stack[:,:,:,color_index],
                                chunks=(1, channel_stack.shape[1], channel_stack.shape[2]),
                                maxshape=(None, channel_stack.shape[1], channel_stack.shape[2]),
                                compression="gzip", shuffle=True, fletcher32=True)

                h5ds.attrs.create('plane', image_planes[color_index])

                # write the data even though we have more to write (free up memory)
                h5f.flush()

    return

# when using this script as a function and not as a library the following will execute
if __name__ == "__main__":
    # hardcoded parameters
    load_metadata = True
    load_channel_masks = True

    # number between 0 and 9, 0 is no compression, 9 is most compression.
    tif_compress = 3

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
    information ('Loading experiment parameters.')
    with open(param_file_path, 'r') as param_file:
        p = yaml.safe_load(param_file) # load parameters into dictionary

    mm3.init_mm3_helpers(param_file_path) # initialized the helper library

    # set up how to manage cores for multiprocessing
    cpu_count = multiprocessing.cpu_count()
    num_analyzers = cpu_count*2 - 2

    # assign shorthand directory names
    TIFF_dir = p['experiment_directory'] + p['image_directory'] # source of images
    ana_dir = p['experiment_directory'] + p['analysis_directory']
    chnl_dir = p['experiment_directory'] + p['analysis_directory'] + 'channels/'
    hdf5_dir = p['experiment_directory'] + p['analysis_directory'] + 'hdf5/'

    # create the subfolders if they don't
    if not os.path.exists(ana_dir):
        os.makedirs(ana_dir)

    if p['output'] == 'TIFF':
        if not os.path.exists(chnl_dir):
            os.makedirs(chnl_dir)
    elif p['output'] == 'HDF5':
        if not os.path.exists(hdf5_dir):
            os.makedirs(hdf5_dir)

    # declare information variables
    analyzed_imgs = {} # for storing get_params pool results.
    written_imgs = {} # for storing write objects set to write. Are removed once written

    ### process TIFFs for metadata #################################################################
    if load_metadata:
        information("Loading image parameters dictionary.")

        with open(ana_dir + '/TIFF_metadata.pkl', 'r') as tiff_metadata:
            analyzed_imgs = pickle.load(tiff_metadata)

    else:
        information("Finding image parameters.")

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
        information('Saving metadata from analyzed images...')
        with open(ana_dir + '/TIFF_metadata.pkl', 'wb') as tiff_metadata:
            pickle.dump(analyzed_imgs, tiff_metadata)
        with open(ana_dir + '/TIFF_metadata.txt', 'w') as tiff_metadata:
            pprint(analyzed_imgs, stream=tiff_metadata)
        information('Saved metadata from analyzed images.')

    ### Make consensus channel masks and get other shared metadata #################################
    if load_channel_masks:
        information("Loading channel masks dictionary.")

        with open(ana_dir + '/channel_masks.pkl', 'r') as cmask_file:
            channel_masks = pickle.load(cmask_file)

    else:
        information("Calculating channel masks.")

        # Uses channel information from the already processed image data
        channel_masks = mm3.make_masks(analyzed_imgs)

        #save the channel mask dictionary to a pickle and a text file
        with open(ana_dir + '/channel_masks.pkl', 'wb') as cmask_file:
            pickle.dump(channel_masks, cmask_file)
        with open(ana_dir + '/channel_masks.txt', 'w') as cmask_file:
            pprint(channel_masks, stream=cmask_file)

        information("Channel masks saved.")

    ### Slice and write TIFF files into channels ###################################################
    information("Saving channel slices.")

    # do it by FOV. Not set up for multiprocessing
    for fov, peaks in channel_masks.iteritems():
        information("Loading channels for FOV %03d." % fov)

        # get filenames just for this fov along with the julian date of acquistion
        send_to_write = [[k, v['jd']] for k, v in analyzed_imgs.items() if v['fov'] == fov]

        # sort the filenames by jdn
        send_to_write = sorted(send_to_write, key=lambda time: time[1])

        if p['output'] == 'TIFF':
            ### This is for loading the whole raw tiff stack and then slicing through it
            tiff_stack_slice_and_write(send_to_write, channel_masks)

            '''
            ### This is for writing each file one at a time.
            # this is really slow do to file opening and closing but less memory hogging
            # writing out each time point
            for fn, jd in send_to_write:
                # get the image parameter dictionary from the analyzed image dict.
                image_params = analyzed_imgs[fn]

                # send to function which slices and writes channels out
                tiff_slice_and_write(image_params, channel_masks)
            '''
        elif p['output'] == 'HDF5':
            # Or write it to hdf5
            hdf5_stack_slice_and_write(send_to_write, channel_masks)

    information("Channel slices saved.")