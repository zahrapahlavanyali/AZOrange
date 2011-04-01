# Do Mahalanobis calculations
# The sole modification is to replace the import ot PyDroneConstants

import os,sys,time
import numpy
# Global variables needed to avoid importing PyDroneConstants
TRAIN = "_train"
NEAREST_DIST = "_dist_near"
NEAREST_ID = "_id_near"
NEAREST_SMI = "_SMI_near"
NEAREST_MEASURED = "_exp_near"

def createInvCovMat(data, ICM_file=None, TSDT_file=None, C_file=None):
    from AZutilities import similarityMetrics
    import orange

    if data.hasMissingValues():
        averageImputer = orange.ImputerConstructor_average(data)
        data = averageImputer(data)
    training_set = similarityMetrics.getTrainingSet(data)

    # Calculate the Inv Cov Matrix and center
    covarMat = numpy.cov(numpy.asarray(training_set.data_table), rowvar=0)
    inverse_covarMat = numpy.linalg.pinv(covarMat, rcond=1e-10)
    center =  numpy.average(training_set.data_table, 0)

    #Save the respective files
    if ICM_file:
        numpy.save(ICM_file, inverse_covarMat)
    if TSDT_file:
        numpy.save(TSDT_file, training_set.data_table)
    if C_file:
        numpy.save(C_file, center)

    return inverse_covarMat

class DummyTrainingSet():
    def __init__(self, dataTableFile):
        self.data_table = numpy.load(dataTableFile)
        dataLen = len(self.data_table) 
        self.measured_list = None
        self.id_list = ["XX"] * dataLen
        self.smiles_list = ["XXX"] * dataLen
                

class MahalanobisDistanceCalculator:
    def __init__(self, training_set = None, invCovMatFile = None, centerFile = None, dataTableFile = None):
        self.norm = None
        self.centre = None

        if (invCovMatFile is not None and not os.path.isfile(invCovMatFile)):
            raise Exception("Cannot locate the Inv. Cov. Matrix file: "+str(invCovMatFile))
        if (centerFile is not None and not os.path.isfile(centerFile)):
            raise Exception("Cannot locate the Center file: "+str(centerFile))
        if (dataTableFile is not None and not os.path.isfile(dataTableFile)):
            raise Exception("Cannot locate the dataTable file: "+str(dataTableFile))

        self.invCovMatFile = invCovMatFile
        self.centerFile = centerFile
        self.dataTableFile = dataTableFile
        
        if self.dataTableFile:
            self.training_set = DummyTrainingSet(self.dataTableFile)
        else:
            assert training_set is not None, "Mahalanobis distance calculator needs a training set in order to do anything useful. Was passed None."
            self.training_set  = training_set

        
    def get_norm(self):
        if self.norm is None:
            self._lazy_init()
        return self.norm
        
    def _lazy_init(self):
        print "Calculating inv Cov Matrix..."
        start = time.time()

        if self.invCovMatFile:
            self.norm = numpy.load(self.invCovMatFile)
        else:    
            self.norm = create_inverse_covariance_norm(self.training_set.data_table)

        if self.centerFile:
            self.center = numpy.load(self.centerFile)
        else:
            self.center = average_vector(self.training_set.data_table)

        print "  ",time.time()-start


    def calculateDistances(self, descriptor_values, count):
        if self.norm is None:
            self._lazy_init()

        # Check that it was a valid computation, and that all values
        # contain non-numbers.  For example, selma may # return
        # "MIS_VAL" in some cases.  Return errors for # non-numbers
        if descriptor_values is None:
            return dict.fromkeys(_name_extensions(count))

        # There are values so compute the distances
        try:
            return self._descriptor_distances(descriptor_values, count)
        except TypeError, details:
            # There are two possibities here:
            #  1) the descriptors didn't all contain numbers
            #      (eg, selma may return MIS_VAL for some descriptors)
            #      In this case, set everything to the error value
            #  2) coding problem.
            #      In this case, raise an error

            # I could check for errors before-hand but in most cases
            # there aren't errors.  Should do performance timings to
            # see which way is faster.
            for value in descriptor_values:
                if not isinstance(value, (float, int)):
                    # Unexpected data type; no wonder there was an error
                    return dict.fromkeys(_name_extensions(count))
            raise
            
    
    def _descriptor_distances(self, v, count):
        """ v is descriptor values for compound. count is number of neighbours we're interested in."""
        # Figure Pierre's scaling factor
        scale = (15.0 / len(v)) ** 0.5
    
        # Store the elements in this dict
        d = {}
    
        # First, compute the distance to the center
        d["_MD"] = compute_distance(v, self.center, self.norm)
        
        # Now, the distance to the training set
        distances = compute_distances(v, self.training_set.data_table, self.norm)
    
        measured_list = self.training_set.measured_list
        if measured_list is None:
            measured_list = [None]*len(self.training_set.id_list)
        # Turn into a 2-ple of (distance, index, id, SMILES, measured)
        dist_index_list = zip(distances, 
                              range(len(distances)), 
                              self.training_set.id_list, 
                              self.training_set.smiles_list,
                              measured_list)
    
        # Sort, which puts the closest terms first
        dist_index_list.sort()
        # get out information about nearest n. Count is usually 3.
        for i in range(count):
            #if i == 0:
                #name_suffix = "" # no suffix for first nearest.
            #else:
            name_suffix = str(i + 1)
            # Get the closest term, (shortest distance), appropriately scaled
            d["%s" % TRAIN  + NEAREST_DIST + name_suffix] = dist_index_list[i][0] * scale
            # get the ID of the nearest member of the training set
            nearest = dist_index_list[i][2]
            d["%s" % TRAIN  + NEAREST_ID + name_suffix] = nearest
            d["%s" % TRAIN + NEAREST_SMI + name_suffix] = dist_index_list[i][3]
       
            # add the measured value of the nearest member of the training set
            measured = dist_index_list[i][4]
            if measured is not None:
                d["%s" % TRAIN + NEAREST_MEASURED  + name_suffix] = measured
 
        # Get the average of the N nearest terms, appropriately scaled
        nearest = dist_index_list[:count]
        avgdist = mean( [x[0] for x in nearest] )
        d[_nearest_name(count)] = avgdist * scale
        return d
    
def mean(data):
    return sum(data) / len(data)
    
    
def average_vector(vectors):
    #import numpy
    return numpy.average(vectors, 0)


def compute_distance(v1, v2, norm):
    return compute_distances(v1, [v2], norm)[0]

def compute_distances(v, vectors, norm):
    # This will return a list of distances for each vector in the list,
    # in the same order as the input
    #import numpy
    print "---> compute_distances..."
    start = time.time()
    if None in v:
        raise TypeError("'None' found in the compound vector - can't convert to a float" % v) 
    try:
        v = numpy.array(v, numpy.float)
    except ValueError, err:
        for term in v:
            if isinstance(term, basestring):
                raise TypeError("%r cannot be converted to a float"
                                % (term,))
        raise
    diff_v = numpy.subtract(vectors, v)
    transformed_v = numpy.dot(diff_v, norm)
    distances = numpy.sum(transformed_v * diff_v, 1)**0.5
    print time.time()-start
    return distances

def create_inverse_covariance_norm(training_vectors):
    #import numpy   # put here to reduce initial import time
    from numpy import linalg
    covarMat = numpy.cov(numpy.asarray(training_vectors), rowvar=0)
    inverse_covarMat = linalg.pinv(covarMat, rcond=1e-10)
    return inverse_covarMat


def _nearest_name(count):
    """extension to the descriptor name use when doing averaging

    For example, for the average distance to the 3 nearest terms
    then count == 3 and this function returns "average3nearestMD".
    """
    return "_train_av%dnearest" % count

def _name_extensions(count, measured=True):
    extensions = ["_MD", _nearest_name(count)]
    for i in range(count):
        name_suffix = str(i + 1)
        extensions.append("%s" % TRAIN + NEAREST_ID + name_suffix)
        extensions.append("%s" % TRAIN + NEAREST_DIST + name_suffix)
        extensions.append("%s" % TRAIN + NEAREST_SMI + name_suffix)
        if measured:
            extensions.append("%s" % TRAIN + NEAREST_MEASURED  + name_suffix)
                  
    return extensions

if __name__ == "__main__":
    test()
