#!/usr/bin/env python
# encoding: utf-8
"""Script that uses the `polar2grid` toolbox of modules to take MODIS
hdf4 (.hdf) files and create a properly scaled AWIPS compatible NetCDF file.

:author:       Eva Schiffer (evas)
:contact:      eva.schiffer@ssec.wisc.edu
:organization: Space Science and Engineering Center (SSEC)
:copyright:    Copyright (c) 2013 University of Wisconsin SSEC. All rights reserved.
:date:         Jan 2013
:license:      GNU GPLv3

Copyright (C) 2013 Space Science and Engineering Center (SSEC),
 University of Wisconsin-Madison.

   This program is free software: you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation, either version 3 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program.  If not, see <http://www.gnu.org/licenses/>.

This file is part of the polar2grid software package. Polar2grid takes
satellite observation data, remaps it, and writes it to a file format for
input into another program.
Documentation: http://www.ssec.wisc.edu/software/polar2grid/

    Written by David Hoese    January 2013
    University of Wisconsin-Madison 
    Space Science and Engineering Center
    1225 West Dayton Street
    Madison, WI  53706
    david.hoese@ssec.wisc.edu

"""
__docformat__ = "restructuredtext en"

from polar2grid.core import Workspace
from polar2grid.core.glue_utils import setup_logging,create_exc_handler,remove_file_patterns
from polar2grid.core.time_utils import utc_now
from polar2grid.core.constants import *
from .grids.grids import create_grid_jobs, Cartographer
from polar2grid.modis import Frontend
import remap
from .awips import Backend
from polar2grid.modis import GEO_FILE_GROUPING

import os
import sys
import logging
from multiprocessing import Process
from datetime import datetime
import re
from collections import defaultdict

log = logging.getLogger(__name__)
GLUE_NAME = "modis2awips"
LOG_FN = os.environ.get("MODIS2AWIPS_LOG", None) # None interpreted in main

def process_data_sets(filepaths,
                      nav_uid,
                      fornav_D=None, fornav_d=None,
                      fornav_m=True,
                      forced_grid=None,
                      forced_gpd=None, forced_nc=None,
                      create_pseudo=True,
                      num_procs=1,
                      rescale_config=None,
                      backend_config=None
                      ) :
    """Process all the files provided from start to finish,
    from filename to AWIPS NC file.
    
    Note: all files provided are expected to share a navigation source.
    """
    
    status_to_return = STATUS_SUCCESS
    
    # Declare polar2grid components
    cart     = Cartographer()
    frontend = Frontend()
    backend  = Backend(
            rescale_config=rescale_config,
            backend_config=backend_config
            )
    
    # Extract Swaths
    log.info("Extracting swaths...")
    try:
        meta_data = frontend.make_swaths(
                filepaths,
                nav_uid=nav_uid,
                create_fog=create_pseudo,
                cut_bad=True
                )

        # if we weren't able to load any of the swaths... stop now
        if len(meta_data.keys()) <= 0 :
            log.error("Unable to load swaths for any of the bands, quitting...")
            return status_to_return or STATUS_UNKNOWN_FAIL
        
        # for convenience, pull some things out of the meta data
        sat = meta_data["sat"]
        instrument = meta_data["instrument"]
        start_time = meta_data["start_time"]
        band_info = meta_data["bands"]
        flatbinaryfilename_lat = meta_data["fbf_lat"]
        flatbinaryfilename_lon = meta_data["fbf_lon"]
        
        log.debug("band_info after prescaling: " + str(band_info.keys()))
    except StandardError:
        log.error("Swath creation failed")
        log.debug("Swath creation error:", exc_info=1)
        status_to_return |= STATUS_FRONTEND_FAIL
        return status_to_return
    
    # Determine grids
    try:
        log.info("Determining what grids the data fits in...")
        grid_jobs = create_grid_jobs(sat, instrument, band_info,
                backend, cart,
                forced_grids=forced_grid,
                fbf_lat=flatbinaryfilename_lat, fbf_lon=flatbinaryfilename_lon)
    except StandardError:
        log.debug("Grid Determination error:", exc_info=1)
        log.error("Determining data's grids failed")
        status_to_return |= STATUS_GDETER_FAIL
        return status_to_return
    
    ### Remap the data
    try:
        remapped_jobs = remap.remap_bands(sat, instrument, nav_uid,
                flatbinaryfilename_lon, flatbinaryfilename_lat, grid_jobs,
                num_procs=num_procs, fornav_d=fornav_d, fornav_D=fornav_D,
                lat_fill_value=meta_data.get("lat_fill_value", None),
                lon_fill_value=meta_data.get("lon_fill_value", None),
                do_single_sample=fornav_m, 
                )
    except StandardError:
        log.debug("Remapping Error:", exc_info=1)
        log.error("Remapping data failed")
        status_to_return |= STATUS_REMAP_FAIL
        return status_to_return
    
    ### BACKEND ###
    W = Workspace('.')
    # for each grid
    for grid_name, grid_dict in remapped_jobs.items():
        
        # process each band for this grid
        for band_kind, band_id in grid_dict.keys():
            
            band_dict = grid_dict[(band_kind, band_id)]
            
            log.info("Running AWIPS backend for %s%s band grid %s" % (band_kind, band_id, grid_name))
            try:
                # Get the data from the flat binary file
                data = getattr(W, band_dict["fbf_remapped"].split(".")[0]).copy()
                
                # Call the backend
                backend.create_product(
                        sat,
                        instrument,
                        band_kind,
                        band_id,
                        band_dict["data_kind"],
                        data,
                        start_time=start_time,
                        grid_name=grid_name,
                        ncml_template=forced_nc or None,
                        fill_value=band_dict.get("fill_value", None)
                        )
            except StandardError:
                log.error("Error in the AWIPS backend for %s%s in grid %s" % (band_kind, band_id, grid_name))
                log.debug("AWIPS backend error:", exc_info=1)
                del remapped_jobs[grid_name][(band_kind, band_id)]
        
        # if all the jobs for a grid failed, warn the user and take that grid off the list
        if len(remapped_jobs[grid_name]) == 0 :
            log.error("All backend jobs for grid %s failed" % (grid_name,))
            del remapped_jobs[grid_name]
    
    # if remapping failed for all grids, warn the user
    if len(remapped_jobs) == 0:
        log.warning("AWIPS backend failed for all grids for bands %r" % (band_info.keys(),))
        status_to_return |= STATUS_BACKEND_FAIL
    
    log.info("Processing of bands %r is complete" % (band_info.keys(),))
    
    return status_to_return

def _process_data_sets(*args, **kwargs):
    """Wrapper function around `process_data_sets` so that it can called
    properly from `run_modis2awips`, where the exitcode is the actual
    returned value from `process_data_sets`.

    This function also checks for exceptions other than the ones already
    checked for in `process_data_sets` so that they are
    recorded properly.
    """
    try:
        stat = process_data_sets(*args, **kwargs)
        sys.exit(stat)
    except MemoryError:
        log.error("%s ran out of memory, check log file for more info" % (GLUE_NAME,))
        log.debug("Memory error:", exc_info=1)
    except OSError:
        log.error("%s had a OS error, check log file for more info" % (GLUE_NAME,))
        log.debug("OS error:", exc_info=1)
    except StandardError:
        log.error("%s had an unexpected error, check log file for more info" % (GLUE_NAME,))
        log.debug("Unexpected/Uncaught error:", exc_info=1)
    except KeyboardInterrupt:
        log.info("%s was cancelled by a keyboard interrupt" % (GLUE_NAME,))
    
    sys.exit(-1)

def run_glue(filepaths,
        multiprocess=True, **kwargs
        ):
    """Separate input files into groups that share navigation files data.
    
    Call the processing function in separate process or same process depending
    on value of `multiprocess` keyword.
    """
    # Rewrite/force parameters to specific format
    filepaths = [ os.path.abspath(os.path.expanduser(x)) for x in sorted(filepaths) ]
    
    # separate these by the MODIS types
    nav_file_type_sets = defaultdict(dict)
    all_used           = set([ ])
    # for each of the possible navigation types
    for nav_file_pattern_key in GEO_FILE_GROUPING.keys() :
        # for each file pattern that uses that navigation type
        for file_pattern in GEO_FILE_GROUPING[nav_file_pattern_key] :
            # add the files matching that pattern to the appropriate set
            nav_file_type_sets[nav_file_pattern_key][file_pattern] = set([ ]) if file_pattern not in nav_file_type_sets[nav_file_pattern_key] else nav_file_type_sets[nav_file_pattern_key][file_pattern]
            nav_file_type_sets[nav_file_pattern_key][file_pattern].update(set([ x for x in filepaths if re.match(file_pattern, os.path.split(x)[1]) ]))
            all_used.update(nav_file_type_sets[nav_file_pattern_key][file_pattern]) # keep a running set of all the files we've found matching nav files for
    all_provided = set(filepaths)
    not_used = all_provided - all_used
    if len(not_used):
        log.warning("Didn't know what to do with\n%s" % "\n".join(list(not_used)))
    
    # some things that we'll use later for clean up
    processes_to_wait_for = defaultdict(list)
    exit_status           = 0
    
    # go through and process each of our file sets by navigation type
    for geo_nav_key in nav_file_type_sets.keys() :
        log.debug("Processing files for %s navigation" % geo_nav_key)
        
        temp_files_for_this_nav = nav_file_type_sets[geo_nav_key]
        try:
            if multiprocess:
                temp_processes = Process(target=_process_data_sets,
                                         args = (temp_files_for_this_nav, geo_nav_key),
                                         kwargs = kwargs
                                         )
                temp_processes.start()
                processes_to_wait_for[geo_nav_key].append(temp_processes)
            else:
                stat = _process_data_sets(temp_files_for_this_nav, geo_nav_key **kwargs)
                exit_status = exit_status or stat
        except StandardError:
            log.error("Could not process files for %s navigation" % geo_nav_key)
            exit_status = exit_status or len(temp_files_for_this_nav)
    
    log.debug("Waiting for subprocesses")
    # look through our processes and wait for any processes we saved to wait for
    for geo_nav_key in processes_to_wait_for.keys() :
        for each_process in processes_to_wait_for[geo_nav_key] :
            each_process.join()
            stat = each_process.exitcode
            exit_status = exit_status or stat
    
    return exit_status

def main(argv = sys.argv[1:]):
    import argparse
    description = """
    Create MODIS swaths, remap them to a grid, and place that remapped data
    into a AWIPS compatible netcdf file.
    """
    parser = argparse.ArgumentParser(description=description)
    
    # Logging related
    parser.add_argument('-v', '--verbose', dest='verbosity', action="count", default=0,
            help='each occurrence increases verbosity 1 level through ERROR-WARNING-INFO-DEBUG (default INFO)')
    parser.add_argument('-l', '--log', dest="log_fn", default=None,
            help="""specify the log filename, default
<gluescript>_%Y%m%d_%H%M%S. Date information is provided from data filename
through strftime. Current time if no files.""")
    parser.add_argument('--debug', dest="debug_mode", default=False,
            action='store_true',
            help="Enter debug mode. Keeping intermediate files.")
    parser.add_argument('--fornav-D', dest='fornav_D', default=10,
            help="Specify the -D option for fornav")
    parser.add_argument('--fornav-d', dest='fornav_d', default=1,
            help="Specify the -d option for fornav")
    parser.add_argument('--fornav-m', dest='fornav_m', default=False, action='store_true',
            help="Specify the -m option for fornav")
    parser.add_argument('--sp', dest='single_process', default=False, action='store_true',
            help="Processing is sequential instead of one process per navigation group")
    parser.add_argument('--num-procs', dest="num_procs", default=1,
            help="Specify number of processes that can be used to run ll2cr/fornav calls in parallel")
    
    # Frontend and product filtering related
    parser.add_argument('--no-pseudo', dest='create_pseudo', default=True, action='store_false',
            help="Don't create pseudo bands")
    
    # Remapping/Grids
    parser.add_argument('-g', '--grids', dest='forced_grids', nargs="+", default=["all"],
            help="Force remapping to only some grids, defaults to 'all', use 'all' for determination")
    parser.add_argument('--gpd', dest='forced_gpd', default=None,
            help="Specify a different gpd file to use")
    
    # Backend Specific
    parser.add_argument('--nc', dest='forced_nc', default=None,
            help="Specify a different ncml file to use")
    parser.add_argument('--backend-config', dest='backend_config', default=None,
            help="specify alternate backend configuration file")
    parser.add_argument('--rescale-config', dest='rescale_config', default=None,
            help="specify alternate rescale configuration file")
    
    # Input related
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-f', dest='data_files', nargs="+",
            help="List of one or more hdf files")
    group.add_argument('-d', dest='data_dir', nargs="?",
            help="Data directory to look for input data files")
    group.add_argument('-R', dest='remove_prev', default=False, action='store_true',
            help="Delete any files that may conflict with future processing. Processing is not done with this flag.")

    
    args = parser.parse_args(args=argv)

    # Figure out what the log should be named
    log_fn = args.log_fn
    if args.remove_prev:
        # They didn't need to specify a filename
        if log_fn is None: log_fn = GLUE_NAME + "_removal.log"
        file_start_time = utc_now()
    else:
        # Get input files and the first filename for the logging datetime
        if args.data_files:
            hdf_files = args.data_files[:]
        elif args.data_dir:
            base_dir = os.path.abspath(os.path.expanduser(args.data_dir))
            hdf_files = [ os.path.join(base_dir,x) for x in os.listdir(base_dir) if x.endswith(".hdf") ]
        else:
            # Should never get here because argparse mexc group
            log.error("Wrong number of arguments")
            parser.print_help()
            return -1
    
        # Handle the user using a '~' for their home directory
        hdf_files = [ os.path.realpath(os.path.expanduser(x)) for x in sorted(hdf_files) ]
        for hdf_file in hdf_files:
            if not os.path.exists(hdf_file):
                print "ERROR: File '%s' doesn't exist" % (hdf_file,)
                return -1
        first_file = os.path.split(hdf_files[0])[-1]

        # Get the date of the first file if provided
        # FIXME: Comment that shows the filename example
        file_start_time = utc_now() # FIXME: Get date from filename

    # Determine the log filename
    if log_fn is None: log_fn = GLUE_NAME + "_%Y%m%d_%H%M%S.log"
    log_fn = datetime.strftime(file_start_time, log_fn)

    levels = [logging.ERROR, logging.WARN, logging.INFO, logging.DEBUG]
    setup_logging(console_level=levels[min(3, args.verbosity)], log_filename=log_fn)
    
    # Don't set this up until after you have setup logging
    sys.excepthook = create_exc_handler(GLUE_NAME)
    
    # Remove previous intermediate and product files
    if args.remove_prev:
        log.info("Removing any possible conflicting files")
        remove_file_patterns(
                Frontend.removable_file_patterns,
                remap.removable_file_patterns,
                Backend.removable_file_patterns
                )
        return 0

    fornav_D = int(args.fornav_D)
    fornav_d = int(args.fornav_d)
    fornav_m = args.fornav_m
    num_procs = int(args.num_procs)
    forced_grids = args.forced_grids
    # Assumes 'all' doesn't appear in the list twice
    if 'all' in forced_grids: forced_grids[forced_grids.index('all')] = None
    if args.forced_gpd is not None:
        args.forced_gpd = os.path.realpath(os.path.expanduser(args.forced_gpd))
        if not os.path.exists(args.forced_gpd):
            log.error("Specified gpd file does not exist '%s'" % args.forced_gpd)
            return -1
    if args.forced_nc is not None:
        args.forced_nc = os.path.realpath(os.path.expanduser(args.forced_nc))
        if not os.path.exists(args.forced_nc):
            log.error("Specified nc file does not exist '%s'" % args.forced_nc)
            return -1
    
    stat = run_glue(hdf_files,
                fornav_D=fornav_D, fornav_d=fornav_d, fornav_m=fornav_m,
                forced_grid=forced_grids,
                forced_gpd=args.forced_gpd, forced_nc=args.forced_nc,
                create_pseudo=args.create_pseudo,
                multiprocess=not args.single_process, num_procs=num_procs,
                rescale_config=args.rescale_config,
                backend_config=args.backend_config
                )
    log.debug("Processing returned status code: %d" % stat)

    # Remove intermediate files (not the backend)
    if not stat and not args.debug_mode:
        log.info("Removing intermediate products")
        remove_file_patterns(
                Frontend.removable_file_patterns,
                remap.removable_file_patterns
                )

    return stat

if __name__ == "__main__":
    sys.exit(main())
