import re
import sys
import urllib
from proxycache import panda_proxy_cache
from taskbuffer import EventServiceUtils


# constants
TimeOutToken = "TimeOut"
NoJobsToken  = "NoJobs"       

########### status codes
# succeeded
SC_Success   =  0
# timeout
SC_TimeOut   = 10
# no available jobs
SC_NoJobs    = 20
# failed
SC_Failed    = 30
# Not secure connection
SC_NonSecure = 40
# invalid token
SC_Invalid   = 50
# invalid role
SC_Role      = 60
# permission denied
SC_Perms     = 70
# key missing
SC_MissKey   = 80


# response
class Response:
    # constructor
    def __init__(self,statusCode,errorDialog=None):
        # create data object
        self.data = {'StatusCode':statusCode}
        if errorDialog != None:
            self.data['errorDialog'] = errorDialog


    # URL encode
    def encode(self):
        return urllib.urlencode(self.data)


    # append Node
    def appendNode(self,name,value):
        self.data[name]=value
            
                   
    # append job
    def appendJob(self,job):
        # PandaID
        self.data['PandaID'] = job.PandaID
        # prodSourceLabel
        self.data['prodSourceLabel'] = job.prodSourceLabel
        # swRelease
        self.data['swRelease'] = job.AtlasRelease
        # homepackage
        self.data['homepackage'] = job.homepackage
        # transformation
        self.data['transformation'] = job.transformation
        # job name
        self.data['jobName'] = job.jobName
        # job definition ID
        self.data['jobDefinitionID'] = job.jobDefinitionID
        # cloud
        self.data['cloud'] = job.cloud
        # files
        strIFiles = ''
        strOFiles = ''
        strDispatch = ''
        strDisToken = ''
        strDisTokenForOutput = ''                
        strDestination = ''
        strRealDataset = ''
        strRealDatasetIn = ''        
        strDestToken = ''
        strProdToken = ''
        strProdTokenForOutput = ''
        strGUID = ''
        strFSize = ''
        strCheckSum = ''
        strFileDestinationSE = ''
        strScopeIn  = ''
        strScopeOut = ''
        strScopeLog = ''        
        logFile = ''
        logGUID = ''        
        for file in job.Files:
            if file.type == 'input':
                if strIFiles != '':
                    strIFiles += ','
                strIFiles += file.lfn
                if strDispatch != '':
                    strDispatch += ','
                strDispatch += file.dispatchDBlock
                if strDisToken != '':
                    strDisToken += ','
                strDisToken += file.dispatchDBlockToken
                if strProdToken != '':
                    strProdToken += ','
                strProdToken += file.prodDBlockToken
                if strGUID != '':
                    strGUID += ','
                strGUID += file.GUID
                strRealDatasetIn += '%s,' % file.dataset
                strFSize += '%s,' % file.fsize
                if not file.checksum in ['','NULL',None]:
                    strCheckSum += '%s,' % file.checksum
                else:
                    strCheckSum += '%s,' % file.md5sum
                strScopeIn += '%s,' % file.scope    
            if file.type == 'output' or file.type == 'log':
                if strOFiles != '':
                    strOFiles += ','
                strOFiles += file.lfn
                if strDestination != '':
                    strDestination += ','
                strDestination += file.destinationDBlock
                if strRealDataset != '':
                    strRealDataset += ','
                strRealDataset += file.dataset
                strFileDestinationSE += '%s,' % file.destinationSE
                if file.type == 'log':
                    logFile = file.lfn
                    logGUID = file.GUID
                    strScopeLog = file.scope
                else:
                    strScopeOut += '%s,' % file.scope                        
                if strDestToken != '':
                    strDestToken += ','
                strDestToken += file.destinationDBlockToken.split(',')[0]
                strDisTokenForOutput += '%s,' % file.dispatchDBlockToken
                strProdTokenForOutput += '%s,' % file.prodDBlockToken
        # inFiles
        self.data['inFiles'] = strIFiles
        # dispatch DBlock
        self.data['dispatchDblock'] = strDispatch
        # dispatch DBlock space token
        self.data['dispatchDBlockToken'] = strDisToken
        # dispatch DBlock space token for output
        self.data['dispatchDBlockTokenForOut'] = strDisTokenForOutput[:-1]
        # outFiles
        self.data['outFiles'] = strOFiles
        # destination DBlock
        self.data['destinationDblock'] = strDestination
        # destination DBlock space token
        self.data['destinationDBlockToken'] = strDestToken
        # prod DBlock space token
        self.data['prodDBlockToken'] = strProdToken
        # real output datasets
        self.data['realDatasets'] = strRealDataset
        # real output datasets
        self.data['realDatasetsIn'] = strRealDatasetIn[:-1]
        # file's destinationSE
        self.data['fileDestinationSE'] = strFileDestinationSE[:-1]
        # log filename
        self.data['logFile'] = logFile
        # log GUID
        self.data['logGUID'] = logGUID
        # jobPars
        self.data['jobPars'] = job.jobParameters
        # attempt number
        self.data['attemptNr'] = job.attemptNr
        # GUIDs
        self.data['GUID'] = strGUID
        # checksum
        self.data['checksum'] = strCheckSum[:-1]
        # fsize
        self.data['fsize'] = strFSize[:-1]
        # scope
        self.data['scopeIn']  = strScopeIn[:-1]
        self.data['scopeOut'] = strScopeOut[:-1]
        self.data['scopeLog'] = strScopeLog
        # destinationSE
        self.data['destinationSE'] = job.destinationSE
        # user ID
        self.data['prodUserID'] = job.prodUserID
        # CPU count
        self.data['maxCpuCount'] = job.maxCpuCount
        # RAM count
        self.data['minRamCount'] = job.minRamCount
        # disk count
        self.data['maxDiskCount'] = job.maxDiskCount
        # cmtconfig
        self.data['cmtConfig'] = job.cmtConfig
        # processingType
        self.data['processingType'] = job.processingType
        # transferType
        self.data['transferType'] = job.transferType
        # current priority
        self.data['currentPriority'] = job.currentPriority
        # taskID
        self.data['taskID'] = job.taskID
        # core count
        self.data['coreCount'] = job.coreCount
        # jobsetID
        self.data['jobsetID'] = job.jobsetID
        # debug mode
        if job.specialHandling != None and 'debug' in job.specialHandling:
            self.data['debug'] = 'True'
        # event service
        if EventServiceUtils.isEventServiceJob(job):
            self.data['eventService'] = 'True'
            # prod DBlock space token for pre-merging output
            self.data['prodDBlockTokenForOutput'] = strProdTokenForOutput[:-1]
        # event service merge
        if EventServiceUtils.isEventServiceMerge(job):
            self.data['eventServiceMerge'] = 'True'
            # write to file
            writeToFileStr = ''
            try:
                for outputName,inputList in job.metadata.iteritems():
                    writeToFileStr += 'inputFor_{0}:'.format(outputName)
                    for tmpInput in inputList:
                        writeToFileStr += '{0},'.format(tmpInput)
                    writeToFileStr = writeToFileStr[:-1]
                    writeToFileStr += '^'
                writeToFileStr = writeToFileStr[:-1]
            except:
                pass
            self.data['writeToFile'] = writeToFileStr


    # set proxy key
    def setProxyKey(self,proxyKey):
        names = ['credname','myproxy']
        for name in names:
            if proxyKey.has_key(name):
                self.data[name] = proxyKey[name]
            else:
                self.data[name] = ''


    # set user proxy
    def setUserProxy(self,realDN=None,role=None):
        try:
            if realDN == None:
                # remove redundant extensions
                realDN = self.data['prodUserID']
                realDN = re.sub('/CN=limited proxy','',realDN)
                realDN = re.sub('(/CN=proxy)+','',realDN)
            pIF = panda_proxy_cache.MyProxyInterface()
            tmpOut = pIF.retrieve(realDN,role=role)
            # not found
            if tmpOut == None:
                return False,'proxy not found for {0}'.format(realDN)
            # set
            self.data['userProxy'] = tmpOut
            return True,''
        except:
            errtype,errvalue = sys.exc_info()[:2]
            return False,"proxy retrieval failed with {0} {1}".format(errtype.__name__,errvalue)

                

# check if secure connection
def isSecure(req):
    if not req.subprocess_env.has_key('SSL_CLIENT_S_DN'):
        return False
    return True


# get user DN
def getUserDN(req):
    try:
        return req.subprocess_env['SSL_CLIENT_S_DN']
    except:
        return 'None'

                
            
