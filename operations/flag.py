#!/usr/bin/env python
# -*- coding: utf-8 -*-

# This operation for LoSoTo implement a flagging procedure
# WEIGHT: flag-only compliant

import logging
from operations_lib import *
import numpy as np

logging.debug('Loading FLAG module.')

import multiprocessing
inQueue = multiprocessing.JoinableQueue()
outQueue = multiprocessing.Queue()

class multiThread(multiprocessing.Process):
    """
    This class is a working thread which load parameters from a queue and
    run the flagging on a chunk of data
    """

    def __init__(self, inQueue, outQueue):
        multiprocessing.Process.__init__(self)
        self.inQueue = inQueue
        self.outQueue = outQueue

    def run(self):

        while True:
            parms = self.inQueue.get()

            # poison pill
            if parms is None:
                self.inQueue.task_done()
                break

            self.flag(*parms)
            self.inQueue.task_done()

    def flag(self, vals, weights, coord, solType, preflagzeros, maxCycles, maxRms, window, order, maxGap, replace, axisToFlag, selection):

        def smooth(data, times, window = 60., order = 1, max_gap = 5.*60. ):
            """
            Remove a trend from the data
            window = in timestamps, sliding window dimension
            order = 0: remove avg, 1: remove linear, 2: remove cubic
            max_gap = maximum allawed gap
        
            return: detrendized data array
            """
        
            final_data = np.copy(data)
        
            # loop over solution times
            for i, time in enumerate(times):
        
                # get data to smooth (values inside the time window)
                data_array = data[ np.where( abs(times - time) <= window / 2. ) ]
                data_offsets = times[ np.where( abs(times - time) <= window / 2. ) ] - time
        
                # check and remove big gaps in data
                if len( data_offsets ) > 1:
                  ddata_offsets = data_offsets[ 1 : ] - data_offsets[ : -1 ]
                  sel = np.where( ddata_offsets > max_gap )[0]
                  if len( sel ) > 0 :
                    min_data_index = 0
                    max_data_index = len( data_offsets )
                    this_time_index = np.where( abs( data_offsets ) == abs( data_offsets ).min() )[0]
                    # find min and max good indexes for this window
                    for s in sel:
                      if ( s < this_time_index ):
                        min_data_index = s + 1
                      if ( s >= this_time_index ):
                        max_data_index = s + 1
                        break
                    # redefine data arrays
                    data_array = data_array[ min_data_index : max_data_index ]
                    data_offsets = data_offsets[ min_data_index : max_data_index ]

                # smooth
                if len( data_array ) > 1:
                  dim = min( len( data_array ) - 2, order )
                  if ( dim == 0 ):
                    smooth_data = np.median( data_array )
                  else:
                    P = np.zeros( ( len( data_offsets ), dim + 1 ), dtype = data_offsets.dtype )
                    P[ : , 0 ] = 1.
                    if ( dim >= 1 ):
                        P[ : , 1 ] = data_offsets
                    if ( dim >= 2 ):
                        P[ : , 2 ] = data_offsets**2
                    Pt = np.transpose( P )
                    smooth_data = np.dot( np.linalg.inv( np.dot( Pt, P ) ), np.dot( Pt, data_array ) )[0]
                  final_data[i] = smooth_data
        
            return final_data
        ######################################
        
        def outlier_rej(vals, weights, time, max_ncycles = 10, max_rms = 3., window = 60., order = 1, max_gap = 5.*60., replace = False):
            """
            Reject outliers using a running median
            val = the array (avg must be 0)
            weights = the weights to convert into flags
            time = array of seconds
            max_ncycles = maximum number of cycles
            max_rms = number of rms times for outlier flagging
            window, order, max_gap = see "smooth"
            replace = instead of flag it, replace the data point with the smoothed one
        
            return: flags array and final rms
            """
        
            flags = np.zeros(shape=weights.shape, dtype=np.bool)
            orig_flags = np.zeros(shape=weights.shape, dtype=np.bool)
            orig_flags[np.where(weights == 0.)] = True # initialize orig_flags to weights
        
            for i in xrange(max_ncycles):
        
                # smoothing (input with no flags!)
                s = ~orig_flags & ~flags # selecting non-flagged data
                vals_smoothed = smooth(vals[ s ], time[ s ], window, order, max_gap)
                vals_detrend = vals[ s ] - vals_smoothed
                
                # median calc
                rms =  1.4826 * np.median( abs(vals_detrend) )
        
                # rejection  
                new_flags = abs(vals_detrend) > max_rms * rms
                flags[ s ] = new_flags
        
                # all is flagged? break
                if (flags == True).all():
                    rms == 0.
                    break
        
                # median calc
                this_rms =  1.4826 * np.median( abs(vals_detrend[ ~new_flags ]) )
        
                # no flags? break
                if rms - this_rms == 0.:
                    break
        
                # replace flagged values with smoothed ones
                if replace:
                    new_vals = vals[ s ]
                    new_vals[ new_flags ] = vals_smoothed[ new_flags ]
                    vals[ s ] = new_vals
        
            return flags | orig_flags, vals, rms
        ########################################

        if preflagzeros:
            if solType == 'amplitude': weights[np.where(vals == 1)] = 0
            else: weights[np.where(vals == 0)] = 0

        # if phase, then convert to real/imag, run the flagger on those, and convert back to pahses
        # best way to avoid unwrapping
        if solType == 'phase' or solType == 'scalarphase' or solType == 'rotation':
            re = 1. * np.cos(vals)
            im = 1. * np.sin(vals)
            flags_re, re, rms_re = outlier_rej(re, weights, coord[axisToFlag], maxCycles, maxRms, window, order, maxGap, replace)
            flags_im, im, rms_im = outlier_rej(im, weights, coord[axisToFlag], maxCycles, maxRms, window, order, maxGap, replace)
            vals = np.arctan2(im, re)
            flags = flags_re | flags_im
            rms = np.sqrt(rms_re**2 + rms_im**2)
            #flags, vals, rms = outlier_rej(unwrap(vals), weights, coord[axisToFlag], maxCycles, maxRms, window, order, maxGap, replace)
            #vals = (vals+np.pi) % (2*np.pi) - np.pi
        else:
            flags, vals, rms = outlier_rej(vals, weights, coord[axisToFlag], maxCycles, maxRms, window, order, maxGap, replace)
        
        if (len(weights)-np.count_nonzero(weights))/float(len(weights)) == sum(flags)/float(len(flags)):
            logging.debug('Percentage of data flagged/replaced (%s): None' % (removeKeys(coord, axisToFlag)))
        else: 
            logging.debug('Percentage of data flagged/replaced (%s): %.3f -> %.3f %% (rms: %.5f)' \
                % (removeKeys(coord, axisToFlag), 100.*(len(weights)-np.count_nonzero(weights))/len(weights), 100.*sum(flags)/len(flags), rms))

        self.outQueue.put([vals, flags, selection])
        
            
def run( step, parset, H ):

    from h5parm import solFetcher, solWriter

    soltabs = getParSoltabs( step, parset, H )

    axisToFlag = parset.getString('.'.join(["LoSoTo.Steps", step, "Axis"]), '' )
    maxCycles = parset.getInt('.'.join(["LoSoTo.Steps", step, "MaxCycles"]), 5 )
    maxRms = parset.getFloat('.'.join(["LoSoTo.Steps", step, "MaxRms"]), 5. )
    window = parset.getFloat('.'.join(["LoSoTo.Steps", step, "Window"]), 100 )
    order = parset.getInt('.'.join(["LoSoTo.Steps", step, "Order"]), 1 )
    maxGap = parset.getFloat('.'.join(["LoSoTo.Steps", step, "MaxGap"]), 5*60 )
    replace = parset.getBool('.'.join(["LoSoTo.Steps", step, "Replace"]), False )
    preflagzeros = parset.getBool('.'.join(["LoSoTo.Steps", step, "PreFlagZeros"]), False )
    ncpu = parset.getInt('.'.join(["LoSoTo.Ncpu"]), 1 )
    
    if axisToFlag == '':
        logging.error("Please specify axis to flag. It must be a single one.")
        return 1

    if order > 2 or order < 0:
        logging.error("Order must be 0 (mean), 1 (linear), 2 (cubic)")
        return 1

    # start processes for multi-thread
    logging.debug('Spowning %i threads...' % ncpu)
    for i in range(ncpu):
        t = multiThread(inQueue, outQueue)
        t.start()

    for soltab in openSoltabs( H, soltabs ):

        logging.info("Flagging soltab: "+soltab._v_name)

        sf = solFetcher(soltab)
        sw = solWriter(soltab, useCache=True) # remember to flush!

        # axis selection
        userSel = {}
        for axis in sf.getAxesNames():
            userSel[axis] = getParAxis( step, parset, H, axis )
        sf.setSelection(**userSel)

        if axisToFlag not in sf.getAxesNames():
            logging.error('Axis \"'+axis+'\" not found.')
            return 1

        solType = sf.getType()

        # fill the queue (note that sf and sw cannot be put into a queue since they have file references)
        for vals, weights, coord, selection in sf.getValuesIter(returnAxes=axisToFlag, weight=True):
            inQueue.put([vals, weights, coord, solType, preflagzeros, maxCycles, maxRms, window, order, maxGap, replace, axisToFlag, selection])

        # add poison pills to kill processes
        for i in range(ncpu):
            inQueue.put(None)

        # wait for all jobs to finish
        inQueue.join()
        
        # writing back the solutions
        while outQueue.empty() != True:
            vals, flags, selection = outQueue.get()
            sw.selection = selection
            if replace:
                # rewrite solutions (flagged values are overwritten)
                sw.setValues(vals, weight=False)
            else:
                # convert boolean flag to 01 binary array (0->flagged)
                sw.setValues((~flags).astype(int), weight=True)

        sw.flush()

        sw.addHistory('FLAG (over %s with %s sigma cut)' % (axisToFlag, maxRms))
    return 0
