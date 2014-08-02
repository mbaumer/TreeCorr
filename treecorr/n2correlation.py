# Copyright (c) 2003-2014 by Mike Jarvis
#
# TreeCorr is free software: redistribution and use in source and binary forms,
# with or without modification, are permitted provided that the following
# conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions, and the disclaimer given in the accompanying LICENSE
#    file.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions, and the disclaimer given in the documentation
#    and/or other materials provided with the distribution.
# 3. Neither the name of the {organization} nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.


import treecorr
import numpy

# Start by loading up the relevant C functions using ctypes
import ctypes
import os

# The numpy version of this function tries to be more portable than the native
# ctypes.cdll.LoadLibary or cdtypes.CDLL functions.
_treecorr = numpy.ctypeslib.load_library('_treecorr',os.path.dirname(__file__))


class N2Correlation(treecorr.BinnedCorr2):
    """This class handles the calculation and storage of a 2-point shear-shear correlation
    function.

    It holds the following attributes:

        logr        The nominal center of the bin in log(r).
        meanlogr    The (weighted) mean value of log(r) for the pairs in each bin.
                    If there are no pairs in a bin, then logr will be used instead.
        xi          The raw correlation function, normalized as npairs / (w1*w2).
        weight      The total weight in each bin.
        npairs      The number of pairs going into each bin.

    The usage pattern is as follows:

        nn = treecorr.N2Correlation(config)
        nn.process(cat1)        # For auto-correlation.
        nn.process(cat1,cat2)   # For cross-correlation.
        nn.write(file_name)     # Write out to a file.
    """
    def __init__(self, config=None, logger=None, **kwargs):
        treecorr.BinnedCorr2.__init__(self, config, logger=None, **kwargs)

        self.xi = numpy.zeros(self.nbins, dtype=float)
        self.ww = 0.

        # an alias
        double_ptr = ctypes.POINTER(ctypes.c_double)

        meanlogr = self.meanlogr.ctypes.data_as(double_ptr)
        weight = self.weight.ctypes.data_as(double_ptr)
        npairs = self.npairs.ctypes.data_as(double_ptr)

        _treecorr.BuildNNCorr.restype = ctypes.c_void_p
        _treecorr.BuildNNCorr.argtypes = [
            ctypes.c_double, ctypes.c_double, ctypes.c_int, ctypes.c_double, ctypes.c_double,
            double_ptr, double_ptr, double_ptr ]

        self.corr = _treecorr.BuildNNCorr(self.min_sep,self.max_sep,self.nbins,self.bin_size,self.b,
                                          meanlogr,weight,npairs);
        logger.debug('Finished building NNCorr')

    def __del__(self):
        # Using memory allocated from the C layer means we have to explicitly deallocate it
        # rather than being able to rely on the Python memory manager.
        if hasattr(self,'data'):    # In case __init__ failed to get that far
            _treecorr.DestroyNNCorr.argtypes = [ ctypes.c_void_p ]
            _treecorr.DestroyNNCorr(self.corr)


    def process_auto(self, cat1):
        """Process a single catalog, accumulating the auto-correlation.

        This accumulates the weighted sums into the bins, but does not finalize
        the calculation by dividing by the total weight at the end.  After
        calling this function as often as desired, the finalize() command will
        finish the calculation.
        """
        self.logger.info('Process N2 auto-correlations for cat %s.',cat1.file_name)
        nfield = cat1.getNField(self.min_sep,self.max_sep,self.b)

        if nfield.sphere:
            _treecorr.ProcessAutoNNSphere.argtypes = [ ctypes.c_void_p, ctypes.c_void_p ]
            _treecorr.ProcessAutoNNSphere(self.corr, nfield.data)
        else:
            _treecorr.ProcessAutoNNFlat.argtypes = [ ctypes.c_void_p, ctypes.c_void_p ]
            _treecorr.ProcessAutoNNFlat(self.corr, nfield.data)

    def process_cross(self, cat1, cat2):
        """Process a single pair of catalogs, accumulating the cross-correlation.

        This accumulates the weighted sums into the bins, but does not finalize
        the calculation by dividing by the total weight at the end.  After
        calling this function as often as desired, the finalize() command will
        finish the calculation.
        """
        self.logger.info('Process N2 cross-correlations for cats %s, %s.',
                         cat1.file_name, cat2.file_name)
        nfield1 = cat1.getNField(self.min_sep,self.max_sep,self.b)
        nfield2 = cat2.getNField(self.min_sep,self.max_sep,self.b)

        if nfield1.sphere != nfield2.sphere:
            raise AttributeError("Cannot correlate catalogs with different coordinate systems.")

        if nfield1.sphere:
            _treecorr.ProcessCrossNNSphere.argtypes = [ 
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_voidp ]
            _treecorr.ProcessCrossNNSphere(self.corr, nfield1.data, nfield2.data)
        else:
            _treecorr.ProcessCrossNNFlat.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_voidp ]
            _treecorr.ProcessCrossNNFlat(self.corr, nfield1.data, nfield2.data)


    def finalize(self):
        """Finalize the calculation of the correlation function.

        The process_auto and process_cross commands accumulate values in each bin,
        so they can be called multiple times if appropriate.  Afterwards, this command
        finishes the calculation by dividing each column by the total weight.
        """
        mask1 = self.npairs != 0
        mask2 = self.npairs == 0

        # The NN arrays need to be normalized by the total possible number of pairs.
        # i.e. nobj1 * nobj2.  This accounts for the possibility that the randoms
        # have a different number of objects than the data.
        if self.ww == 0:
            self.xi = self.weight
        else:
            self.xi = self.weight / self.ww

        self.meanlogr[mask1] /= self.weight[mask1]

        # Update the units of meanlogr
        self.meanlogr[mask1] -= self.log_sep_units

        # Use meanlogr when available, but set to nominal when no pairs in bin.
        self.meanlogr[mask2] = self.logr[mask2]

    def clear(self):
        """Clear the data vectors
        """
        self.meanlogr[:] = 0.
        self.xi[:] = 0.
        self.weight[:] = 0.
        self.npairs[:] = 0.
        self.ww = 0.


    def process(self, cat1, cat2=None):
        """Compute the correlation function.

        If only 1 argument is given, then compute an auto-correlation function.
        If 2 arguments are given, then compute a cross-correlation function.

        Both arguments may be lists, in which case all items in the list are used 
        for that element of the correlation.
        """
        self.clear()
        if not isinstance(cat1,list): cat1 = [cat1]
        if cat2 is not None and not isinstance(cat2,list): cat2 = [cat2]
        if len(cat1) == 0:
            raise ValueError("No catalogs provided for cat1")

        if cat2 is None or len(cat2) == 0:
            if self.config.get('do_auto_corr',False) or len(cat1) == 1:
                for c1 in cat1:
                    self.process_auto(c1)
                    self.ww += 0.5*c1.sumw**2
            if self.config.get('do_cross_corr',True):
                for i,c1 in enumerate(cat1):
                    for c2 in cat1[i+1:]:
                        self.process_cross(c1,c2)
                        self.ww += c1.sumw*c2.sumw
        else:
            for c1 in cat1:
                for c2 in cat2:
                    self.process_cross(c1,c2)
                    self.ww += c1.sumw*c2.sumw
        self.finalize()


    def calculateXi(self, rr, nr=None, rn=None):
        """Calculate the correlation function given another correlation function of random
        points using the same mask, and possibly cross correlations of the data and random.

        For a signal that involves a cross correlations, there should be two random
        cross-correlations: data-random and random-data, given as nr and rn.

        rr is the N2Correlation function for random points.
        If nr is None, the simple correlation function (nn/rr - 1) is used.
        if nr is given and rn is None, then (nn - 2nr + rr)/rr is used.
        If nr and rn are both given, then (nn - nr - rn + rr)/rr is used.
        """
        if nr is None:
            if rn is None:
                xi = (self.xi - rr.xi)
            else:
                xi = (self.xi - 2.*rn.xi + rr.xi)
        else:
            if rn is None:
                xi = (self.xi - 2.*rn.xi + rr.xi)
            else:
                xi = (self.xi - rn.xi - nr.xi + rr.xi)
        if any(rr.xi == 0):
            self.logger.warn("Warning: Some bins for the randoms have zero weight.")
        mask1 = rr.xi != 0
        mask2 = rr.xi == 0
        xi[mask1] /= rr.xi[mask1]
        xi[mask2] = 0

    def write(self, file_name, rr, nr=None, rn=None):
        """Write the correlation function to the file, file_name.

        rr is the N2Correlation function for random points.
        If nr is None, the simple correlation function (nn - rr)/rr is used.
        if nr is given and rn is None, then (nn - 2nr + rr)/rr is used.
        If nr and rn are both given, then (nn - nr - rn + rr)/rr is used.
        """
        self.logger.info('Writing N2 correlations to %s',file_name)
        
        if nr is None and rn is None:
            ncol = 8
        else:
            ncol = 10
            if nr is None: nr = rn
            if rn is None: rn = nr

        output = numpy.empty( (self.nbins, ncol) )
        output[:,0] = numpy.exp(self.logr)
        output[:,1] = numpy.exp(self.meanlogr)
        output[:,2] = self.calculateXi(rr,nr,rn)
        mask1 = rr.xi != 0
        mask2 = rr.xi == 0
        output[mask1,3] = numpy.sqrt(1./rr.xi[mask1])
        output[mask2,3] = 0.
        output[:,4] = self.weight
        output[:,5] = self.npairs
        output[:,6] = self.xi
        output[:,7] = rr.xi
        if ncol == 10:
            output[:,8] = nr.xi
            output[:,9] = rn.xi

        prec = self.config.get('precision',3)
        width = prec+8
        header_form = (ncol-1)*("{:^%d}."%width) + "{:^%d}"%width
        # NB. The last two arguments are silently ignored if ncol = 8.
        header = header_form.format('R_nom','<R>','xi','sigma_xi','weight','npairs',
                                    'NN','RR','NR','RN')
        fmt = '%%%d.%de'%(width,prec)
        numpy.savetxt(file_name, output, fmt=fmt, header=header)

