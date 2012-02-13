'''
setup cloud

'''

import re
import sys
import time
import random
import commands
import brokerage.broker_util
from DDM import ddm
from DDM import dq2Common
from config import panda_config
from taskbuffer import ProcessGroups
from pandalogger.PandaLogger import PandaLogger
import DataServiceUtils


# logger
_logger = PandaLogger().getLogger('TaskAssigner')

# cutoff for RW
thr_RW_low  = 400
thr_RW_high = 8000
thr_RW_sub  = 600

# cutoff for disk
thr_space_low = (1 * 1024)

# task types using MC share
taskTypesMcShare = ['evgen']

# task types for subscriptions
taskTypesSub = ['simul']


class TaskAssigner:
    # constructor
    def __init__(self,taskBuffer,siteMapper,taskID,prodSourceLabel,job):
        self.taskBuffer = taskBuffer
        self.siteMapper = siteMapper
        self.taskID     = taskID
        self.cloudTask  = None
        self.prodSourceLabel = prodSourceLabel
        self.cloudForSubs = []
        self.job = job

    # check cloud
    def checkCloud(self):
        try:
            _logger.debug('%s checkCloud' % self.taskID)
            # get CloudTask from DB
            self.cloudTask = self.taskBuffer.getCloudTask(self.taskID)
            if self.cloudTask == None:
                _logger.error('%s cannot get CloudTask' % self.taskID)
                return None
            # if already assigned
            if self.cloudTask.status == 'assigned':
                _logger.debug('%s checked Cloud -> %s' % (self.taskID,self.cloudTask.cloud))
                return self.cloudTask.cloud
            # return "" to set cloud later
            _logger.debug('%s return Cloud=""' % self.taskID)
            return ""
        except:
            type, value, traceBack = sys.exc_info()
            _logger.error("%s checkCloud : %s %s" % (self.taskID,type,value))
            return None


    # set cloud
    def setCloud(self,lfns,guids,locations={},metadata=None):
        try:
            _logger.debug('%s setCloud' % self.taskID)
            _logger.debug('%s metadata="%s"' % (self.taskID,metadata))
            taskType = None
            RWs      = {}
            expRWs   = {}
            highRWs  = {}
            prioMap  = {}
            fullRWs  = {}
            tt2Map   = {}
            diskCount = 0
            usingOpenDS = False
            try:
                # parse metadata
                if not metadata in (None,'NULL'):
                    # task type
                    taskType = metadata.split(';')[0]
                    # RWs
                    exec "RWs = %s"     % metadata.split(';')[1]
                    # expected RWs
                    exec "expRWs = %s"  % metadata.split(';')[2]
                    # RWs for high priority tasks
                    exec "prioMap = %s" % metadata.split(';')[3]
                    # full RWs for space calcuration
                    exec "fullRWs = %s" % metadata.split(';')[4]
                    # tasktype2 map
                    exec "tt2Map = %s"  % metadata.split(';')[5]
            except:
                pass
            try:
                diskCount = int(self.job.maxDiskCount)
            except:
                pass
            message = '%s taskType==%s prio==%s RW==%s DiskCount==%s' % (self.taskID,taskType,prioMap[self.taskID],
                                                                         expRWs[self.taskID],diskCount)
            _logger.debug(message)
            self.sendMesg(message)
            _logger.debug('%s RWs     = %s' % (self.taskID,str(RWs)))
            _logger.debug('%s expRWs  = %s' % (self.taskID,str(expRWs)))
            _logger.debug('%s prioMap = %s' % (self.taskID,str(prioMap)))            
            _logger.debug('%s fullRWs = %s' % (self.taskID,str(fullRWs)))
            _logger.debug('%s tt2Map  = %s' % (self.taskID,str(tt2Map)))
            # get cloud list
            cloudList = self.siteMapper.getCloudList()
            # get pilot statistics
            nWNmap = self.taskBuffer.getCurrentSiteData()
            # get process group
            myTaskGroup = ProcessGroups.getProcessGroup(tt2Map[self.taskID])
            # recalculate RWs
            for tmpTaskID,tmpExpRW in expRWs.iteritems():
                # skip myself
                if tmpTaskID == self.taskID:
                    continue
                # get cloud from DB
                tmpCloudInDB = self.taskBuffer.seeCloudTask(tmpTaskID)
                # not assigned
                if tmpCloudInDB == '':
                    continue
                # increase full RW
                if not fullRWs.has_key(tmpCloudInDB):
                    fullRWs[tmpCloudInDB] = 0
                fullRWs[tmpCloudInDB] += tmpExpRW
                # no priority info
                if not prioMap.has_key(tmpTaskID):
                    continue
                # lower priority
                if prioMap[tmpTaskID] < prioMap[self.taskID]:
                    continue
                # check tasktype2
                tmpTaskGroup = ProcessGroups.getProcessGroup(tt2Map[tmpTaskID])
                # check tasktype2
                if tmpTaskGroup != myTaskGroup:
                    continue
                # increase RW
                if not RWs.has_key(tmpCloudInDB):
                    RWs[tmpCloudInDB] = 0
                RWs[tmpCloudInDB] += tmpExpRW
            _logger.debug('%s newRWs  =%s' % (self.taskID,str(RWs)))
            _logger.debug('%s fullRWs =%s' % (self.taskID,str(fullRWs)))            
            # remove offline clouds and check validation/fasttrack
            tmpCloudList = []
            for tmpCloudName in cloudList:
                # get cloud
                tmpCloud = self.siteMapper.getCloud(tmpCloudName)
                # skip offline clouds
                if not tmpCloud['status'] in ['online']:
                    message = '%s    %s skip : status==%s' % (self.taskID,tmpCloudName,tmpCloud['status'])
                    _logger.debug(message)
                    self.sendMesg(message)
                    continue
                # skip non-validation cloud if validation
                if self.prodSourceLabel in ['validation'] and tmpCloud['validation'] != 'true':
                    message = "%s    %s skip : validation=='%s'" % (self.taskID,tmpCloudName,tmpCloud['validation'])
                    _logger.debug(message)
                    self.sendMesg(message)
                    continue
                # check fast track
                if ((taskType in ['evgen'] and prioMap[self.taskID] >= 700) or
                    (taskType in ['simul'] and prioMap[self.taskID] >= 800)) and tmpCloud['fasttrack'] != 'true':
                    message = "%s    %s skip : fasttrack=='%s'" % (self.taskID,tmpCloudName,tmpCloud['fasttrack'])
                    _logger.debug(message)
                    self.sendMesg(message)
                    continue
                # check disk count
                if diskCount != 0: 
                    enoughSpace = self.checkDiskCount(diskCount,tmpCloudName)
                    if not enoughSpace:
                        message = "%s    %s skip : no online sites have enough space for DiskCount==%s" % (self.taskID,tmpCloudName,diskCount)
                        _logger.debug(message)
                        self.sendMesg(message,msgType='warning')
                        continue
                # append
                tmpCloudList.append(tmpCloudName)
                self.cloudForSubs.append(tmpCloudName)
            cloudList = tmpCloudList
            # DQ2 location info
            _logger.debug('%s DQ2 locations %s' % (self.taskID,str(locations)))
            # check immutable datasets
            for tmpDataset,tmpSites in locations.iteritems():
                sitesForRefresh = []
                for tmpSite in tmpSites.keys():
                    tmpStat = tmpSites[tmpSite][-1]
                    if tmpStat['total'] == -1 or tmpStat['found'] ==  None:
                        sitesForRefresh.append(tmpSite)
                    elif tmpStat['immutable'] == 0:
                        # using open datasets
                        usingOpenDS = True
                        _logger.debug('%s open dataset : %s' % (self.taskID,tmpDataset))
                # refresh replica info
                if sitesForRefresh != []:
                    # invoke listFileReplicasBySites to refresh replica info
                    _logger.debug('%s listFileReplicasBySites %s:%s' % (self.taskID,tmpDataset,str(sitesForRefresh)))
                    tmpStat,tmpOut = ddm.DQ2_iter.listFileReplicasBySites(tmpDataset,0,sitesForRefresh,0,300)
                    _logger.debug('%s listFileReplicasBySites end with %s:%s' % (self.taskID,tmpStat,tmpOut))
                    #raise RuntimeError, '%s %s has incorrect replica info' % (self.taskID,tmpDataset)
            removedDQ2Map = {}
            t2ListForMissing = {}
            completedCloud = []
            if locations != {}:
                removedCloud = []
                for dataset,sites in locations.iteritems():
                    removedDQ2Map[dataset] = []
                    _logger.debug('%s DS:%s' % (self.taskID,dataset)) 
                    for tmpCloudName in cloudList:
                        tmpCloud = self.siteMapper.getCloud(tmpCloudName)
                        # look for T1 SE which holds the max number of files
                        minFound = -1
                        foundSE  = ''
                        for tmpSE in tmpCloud['tier1SE']:
                            if tmpSE in sites.keys():
                                tmpStat = sites[tmpSE][-1]
                                if tmpStat['found'] == None:
                                    if minFound == -1:
                                        foundSE  = tmpSE
                                elif minFound < tmpStat['found']:
                                    minFound = tmpStat['found']
                                    foundSE  = tmpSE
                        # get list of T2s where dataset is available
                        tmpT2List = DataServiceUtils.getSitesWithDataset(dataset,self.siteMapper,locations,tmpCloudName,True)
                        # remove cloud if T1SE or T2 is not a location
                        if foundSE == '':
                            # keep if T2 has the dataset
                            if tmpT2List == []:
                                if not tmpCloudName in removedCloud:
                                    _logger.debug('%s   removed %s' % (self.taskID,tmpCloudName))
                                    removedCloud.append(tmpCloudName)
                            # add dataset to map for subscription        
                            if not tmpCloudName in removedDQ2Map[dataset]:
                                removedDQ2Map[dataset].append(tmpCloudName)
                        else:
                            # check incomplete or not
                            tmpStat = sites[foundSE][-1]
                            if tmpStat['found'] == None or tmpStat['found'] < tmpStat['total']:
                                # add dataset to map which is subscribed when the task is used due to T2 files
                                if not tmpCloudName in removedDQ2Map[dataset]:
                                    removedDQ2Map[dataset].append(tmpCloudName)
                        # aggregate T2 list
                        if not t2ListForMissing.has_key(tmpCloudName):
                            t2ListForMissing[tmpCloudName] = []
                        for tmpT2 in tmpT2List:
                            if not tmpT2 in t2ListForMissing[tmpCloudName]:
                                t2ListForMissing[tmpCloudName].append(tmpT2)
                # remove clouds
                for tmpCloudName in removedCloud:
                    if tmpCloudName in cloudList:
                        cloudList.remove(tmpCloudName)
            _logger.debug('%s new locations after DQ2 filter %s' % (self.taskID,str(cloudList)))
            if cloudList == []:
                # make subscription to empty cloud
                if taskType in taskTypesSub:
                    _logger.debug('%s makeSubscription start' % self.taskID)                    
                    retSub = self.makeSubscription(removedDQ2Map,RWs,fullRWs,expRWs)
                    _logger.debug('%s makeSubscription end with %s' % (self.taskID,retSub))
                message = '%s no input data locations' % self.taskID
                self.sendMesg(message,msgType='error')
                raise RuntimeError, '%s cloud list is empty after DQ2 filter' % self.taskID
            message = '%s input data locations %s' % (self.taskID,str(cloudList))
            _logger.debug(message)
            self.sendMesg(message)
            # calculate # of loops
            nFile = 200
            nLoop = len(guids) / nFile
            if len(guids) % nFile != 0:
                nLoop += 1
            iFileList = []
            for iTmp in range(nLoop):
                iFileList.append(iTmp*nFile)
            # truncate list to avoid too many lookup     
            maxLoop = 100
            if len(iFileList) > maxLoop:
                random.shuffle(iFileList)
                iFileList = iFileList[:maxLoop]
                iFileList.sort()
            # count the number of files to be lookup
            maxNFiles = 0
            if not usingOpenDS:
                # if dataset is open, doesn't check nFiles
                for iFile in iFileList:
                    maxNFiles += len(lfns[iFile:iFile+nFile])
            # loop over all cloud
            weightParams = {}
            foundCandidateWithT1 = []
            candidatesUsingT2 = []
            for tmpCloudName in cloudList:
                _logger.debug('%s calculate weight for %s' % (self.taskID,tmpCloudName))
                # add missing cloud in RWs
                if not RWs.has_key(tmpCloudName):
                    RWs[tmpCloudName] = 0
                if not fullRWs.has_key(tmpCloudName):
                    fullRWs[tmpCloudName] = 0
                # get cloud
                tmpCloud = self.siteMapper.getCloud(tmpCloudName)
                weightParams[tmpCloudName] = {}            
                # get T1 site
                tmpT1Site = self.siteMapper.getSite(tmpCloud['source'])
                # get number of running jobs. Initially set 1 to avoid zero dividing
                nPilot = 1
                for siteName in tmpCloud['sites']:
                    if nWNmap.has_key(siteName):
                        nPilot += (nWNmap[siteName]['getJob'] + nWNmap[siteName]['updateJob'])
                weightParams[tmpCloudName]['nPilot'] = nPilot
                _logger.debug('%s  # of pilots %s' % (self.taskID,nPilot))
                # available space
                weightParams[tmpCloudName]['space'] = tmpT1Site.space
                _logger.debug('%s  T1 space    %s' % (self.taskID,tmpT1Site.space))
                # MC share
                weightParams[tmpCloudName]['mcshare'] = tmpCloud['mcshare']
                _logger.debug('%s  MC share    %s' % (self.taskID,tmpCloud['mcshare']))
                # calculate available space = totalT1space - ((RW(cloud)+RW(thistask))*GBperSI2kday))
                aveSpace,sizeCloud,sizeThis = self.getAvailableSpace(weightParams[tmpCloudName]['space'],
                                                                     fullRWs[tmpCloudName],
                                                                     expRWs[self.taskID])
                # no task is assigned if available space is less than 1TB
                if aveSpace < thr_space_low:
                    message = '%s    %s skip : space:%s (total:%s - assigned:%s - this:%s) < %sGB' % \
                              (self.taskID,tmpCloudName,aveSpace,weightParams[tmpCloudName]['space'],
                               sizeCloud,sizeThis,thr_space_low)
                    _logger.debug(message)
                    self.sendMesg(message,msgType='warning')
                    del weightParams[tmpCloudName]                    
                    continue
                else:
                    _logger.debug('%s    %s pass : space:%s (total:%s - assigned:%s - this:%s)' % \
                                  (self.taskID,tmpCloudName,aveSpace,weightParams[tmpCloudName]['space'],
                                   sizeCloud,sizeThis))
                # not assign tasks when RW is too high
                if RWs.has_key(tmpCloudName) and RWs[tmpCloudName] > thr_RW_high*weightParams[tmpCloudName]['mcshare']:
                    message = '%s    %s skip : too high RW==%s > %s' % \
                              (self.taskID,tmpCloudName,RWs[tmpCloudName],thr_RW_high*weightParams[tmpCloudName]['mcshare'])
                    _logger.debug(message)
                    self.sendMesg(message,msgType='warning')
                    del weightParams[tmpCloudName]
                    continue
                # DQ2 URL
                dq2URL = tmpT1Site.dq2url
                dq2ID  = tmpT1Site.ddm
                # set LFC and SE name 
                tmpSE = []
                if not tmpT1Site.lfchost in [None,'']:
                    dq2URL = 'lfc://'+tmpT1Site.lfchost+':/grid/atlas/'
                    tmpSE = []
                    if tmpT1Site.se != None:
                        for tmpSrcSiteSE in tmpT1Site.se.split(','):
                            match = re.search('.+://([^:/]+):*\d*/*',tmpSrcSiteSE)
                            if match != None:
                                tmpSE.append(match.group(1))
                # hack for split T1
                if tmpCloudName == 'NL':
                    tmpSplitSite = self.siteMapper.getSite('NIKHEF-ELPROD')
                    if tmpSplitSite.se != None:
                        for tmpSrcSiteSE in tmpSplitSite.se.split(','):
                            match = re.search('.+://([^:/]+):*\d*/*',tmpSrcSiteSE)
                            if match != None: 
                                tmpSE.append(match.group(1))                                
                # get files from LRC
                weightParams[tmpCloudName]['nFiles'] = 0
                # loop over all files
                iLoop = 0
                skipFlag = False
                for iFile in iFileList:
                    nTry = 3
                    for iTry in range(nTry):
                        tmpOKFiles = brokerage.broker_util.getFilesFromLRC(lfns[iFile:iFile+nFile],dq2URL,guids=guids[iFile:iFile+nFile],
                                                                           storageName=tmpSE,terminateWhenFailed=True)
                        # failed
                        if tmpOKFiles == None:
                            if iTry+1 == nTry:
                                message = '%s %s failed to access LFC/LRC' % (self.taskID,tmpCloudName)
                                self.sendMesg(message,msgType='error')
                                raise RuntimeError, 'invalid return from getFilesFromLRC'
                            else:
                                # retry
                                time.sleep(60)
                        else:
                            break
                    # sum    
                    weightParams[tmpCloudName]['nFiles'] += len(tmpOKFiles)
                    # show progress
                    iLoop += 1
                    if iLoop % 10 == 1:
                        _logger.debug('%s %s %s' % (self.taskID,len(guids),iFile))
                # skip 
                if skipFlag:
                    _logger.debug('%s    %s skip : invalid retur from LFC' % (self.taskID,cloudName))
                    del weightParams[tmpCloudName]
                    continue
                _logger.debug('%s  # of files at T1 %s' % (self.taskID,weightParams[tmpCloudName]['nFiles']))
                # found candidate
                if weightParams[tmpCloudName]['nFiles'] >= maxNFiles:
                    foundCandidateWithT1.append(tmpCloudName)
                # check T2 if files are missing and no candidate so far
                if foundCandidateWithT1 == [] and weightParams[tmpCloudName]['nFiles'] < maxNFiles and \
                       t2ListForMissing.has_key(tmpCloudName) and t2ListForMissing[tmpCloudName] != []:
                    # list of SEs and LFC
                    tmpLfcSe = {}
                    for tmpT2 in t2ListForMissing[tmpCloudName]:
                        tmpT2Spec = self.siteMapper.getSite(tmpT2)
                        tmpDq2URL = 'lfc://'+tmpT2Spec.lfchost+':/grid/atlas/'
                        if tmpT2Spec.se != None:
                            for tmpSrcSiteSE in tmpT2Spec.se.split(','):
                                match = re.search('.+://([^:/]+):*\d*/*',tmpSrcSiteSE)
                                if match != None:
                                    if not tmpLfcSe.has_key(tmpDq2URL):
                                        tmpLfcSe[tmpDq2URL] = [] 
                                    tmpLfcSe[tmpDq2URL].append(match.group(1))
                    # loop over all LFC and SEs
                    for tmpDq2URL,tmpSE in tmpLfcSe.iteritems():
                        # loop over all files
                        iLoop = 0
                        nFilesT2 = 0
                        for iFile in iFileList:
                            nTry = 3
                            for iTry in range(nTry):
                                tmpOKFiles = brokerage.broker_util.getFilesFromLRC(lfns[iFile:iFile+nFile],tmpDq2URL,guids=guids[iFile:iFile+nFile],
                                                                                   storageName=tmpSE,terminateWhenFailed=True)
                                if tmpOKFiles == None:
                                    if iTry+1 == nTry:
                                        _logger.debug('%s invalid return from LFC' % self.taskID)
                                        break
                                    else:
                                        # retry
                                        time.sleep(60)
                                else:
                                    break
                            # sum    
                            nFilesT2 += len(tmpOKFiles)
                            # show progress
                            iLoop += 1
                            if iLoop % 10 == 1:
                                _logger.debug('%s %s %s %s T2:%s' % (self.taskID,len(guids),iFile,tmpCloudName,str(tmpSE)))
                        # use larger value
                        _logger.debug('%s  # of files at T2:%s %s' % (self.taskID,str(tmpSE),nFilesT2))
                        if nFilesT2 > weightParams[tmpCloudName]['nFiles']:
                            weightParams[tmpCloudName]['nFiles'] = nFilesT2
                            # found candidate
                            if weightParams[tmpCloudName]['nFiles'] >= maxNFiles:
                                candidatesUsingT2.append(tmpCloudName)
                                break
            # compare parameters
            definedCloud = "US"
            maxClouds = []
            useMcShare = False
            if foundCandidateWithT1 != []:
                maxClouds = foundCandidateWithT1
            elif candidatesUsingT2 != []:
                maxClouds = candidatesUsingT2
            # logging
            _logger.debug('%s check nFiles' % self.taskID)            
            for cloudName,params in weightParams.iteritems():
                if not cloudName in maxClouds:
                    message = '%s    %s skip : nFiles==%s<%s' % \
                              (self.taskID,cloudName,params['nFiles'],maxNFiles)
                    _logger.debug(message)
                    self.sendMesg(message)
                    time.sleep(2)                    
            # check RW
            _logger.debug('%s check RW' % self.taskID)                
            tmpInfClouds = []
            for cloudName in maxClouds:
                # set weight to infinite when RW is too low
                if not taskType in taskTypesMcShare:
                    if RWs[cloudName] < thr_RW_low*weightParams[cloudName]['mcshare']:
                        message = '%s    %s infinite weight : RW==%s < %s' % \
                                  (self.taskID,cloudName,RWs[cloudName],thr_RW_low*weightParams[cloudName]['mcshare'])
                        _logger.debug(message)
                        self.sendMesg(message)
                        tmpInfClouds.append(cloudName)
            # use new list
            if tmpInfClouds != []:
                _logger.debug('%s use infinite clouds after RW checking' % self.taskID)
                maxClouds  = tmpInfClouds
                useMcShare = True
            elif maxClouds == []:
                messageEnd = '%s no candidates left' % self.taskID
                self.sendMesg(messageEnd)
                # make subscription to empty cloud
                if taskType in taskTypesSub:
                    _logger.debug('%s makeSubscription start' % self.taskID)                    
                    retSub = self.makeSubscription(removedDQ2Map,RWs,fullRWs,expRWs)
                    _logger.debug('%s makeSubscription end with %s' % (self.taskID,retSub))
                    if retSub:
                        message = '%s made subscription' % self.taskID
                        self.sendMesg(message,msgType='info')                        
                    else:
                        message = "%s didn't make subscription" % self.taskID
                        self.sendMesg(message,msgType='warning')
                # return
                _logger.debug(messageEnd)
                _logger.debug("%s end" % self.taskID) 
                return None
            # choose one
            message = '%s candidates %s' % (self.taskID,str(maxClouds))
            _logger.debug(message)
            self.sendMesg(message)
            if len(maxClouds) == 1:
                definedCloud = maxClouds[0]
            elif len(maxClouds) > 1:
                # choose cloud according to weight
                nWeightList = []
                totalWeight = 0
                for cloudName in maxClouds:
                    if (taskType in taskTypesMcShare):
                        # use MC share for evgen
                        tmpWeight = float(weightParams[cloudName]['mcshare'])
                        message = "%s %s weight==%s" % (self.taskID,cloudName,weightParams[cloudName]['mcshare'])
                    else:
                        # use nPilot/RW*MCshare
                        tmpWeight = float(weightParams[cloudName]['nPilot']) / float(1+RWs[cloudName])
                        message = "%s %s weight==%s/%s" % (self.taskID,cloudName,
                                                          weightParams[cloudName]['nPilot'],
                                                          1+RWs[cloudName])
                    self.sendMesg(message)
                    nWeightList.append(tmpWeight)
                    totalWeight += tmpWeight
                # check total weight
                if totalWeight == 0:
                    raise RuntimeError, 'totalWeight=0'
                # determin cloud using random number
                _logger.debug('%s weights %s' % (self.taskID,str(nWeightList)))
                rNumber = random.random() * totalWeight
                _logger.debug('%s    totalW   %s' % (self.taskID,totalWeight))                
                _logger.debug('%s    rNumber  %s' % (self.taskID,rNumber))
                for index,tmpWeight in enumerate(nWeightList):
                    rNumber -= tmpWeight
                    _logger.debug('%s    rNumber  %s : Cloud=%s weight=%s' % 
                                  (self.taskID,rNumber,maxClouds[index],tmpWeight))
                    if rNumber <= 0:
                        definedCloud = maxClouds[index]
                        break
            # make subscription when T2 candidate is chosen
            if definedCloud in candidatesUsingT2:
                newT2DQ2Map = {}
                for tmpDS,tmpT2CloudList in removedDQ2Map.iteritems():
                    if definedCloud in tmpT2CloudList:
                        newT2DQ2Map[tmpDS] = [definedCloud]
                if newT2DQ2Map == {}:
                    _logger.error('%s no subscription map to use T2 datasets cloud=%s map=%s' % (self.taskID,definedCloud,removedDQ2Map))
                    return None
                _logger.debug('%s makeSubscription to use T2 start' % self.taskID)                                    
                retSub = self.makeSubscription(newT2DQ2Map,RWs,fullRWs,expRWs,noEmptyCheck=True,acceptInProcess=True)
                if not retSub:
                    _logger.error('%s makeSubscription to use T2 failed with %s' % (self.taskID,retSub))
                    return None
                _logger.debug('%s makeSubscription to use T2 end with %s' % (self.taskID,retSub))
            # set CloudTask in DB
            self.cloudTask.cloud = definedCloud
            retCloudTask = self.taskBuffer.setCloudTask(self.cloudTask)
            if retCloudTask == None:
                _logger.error('%s cannot set CloudTask' % self.taskID)
                return None
            message = '%s set Cloud -> %s' % (self.taskID,retCloudTask.cloud)
            _logger.debug(message)
            self.sendMesg(message)
            # return
            return retCloudTask.cloud
        except:
            type, value, traceBack = sys.exc_info()
            _logger.error("%s setCloud : %s %s" % (self.taskID,type,value))
            return None
            

    # send message to logger
    def sendMesg(self,message,msgType=None):
        try:
            # get logger
            tmpPandaLogger = PandaLogger()
            # lock HTTP handler
            tmpPandaLogger.lock()
            tmpPandaLogger.setParams({'Type':'taskbrokerage'})
            # use bamboo for loggername 
            tmpLogger = tmpPandaLogger.getHttpLogger('bamboo')
            # add message
            if msgType=='error':
                tmpLogger.error(message)
            elif msgType=='warning':
                tmpLogger.warning(message)
            elif msgType=='info':
                tmpLogger.info(message)
            else:
                tmpLogger.debug(message)                
            # release HTTP handler
            tmpPandaLogger.release()
        except:
            pass
        time.sleep(1)


    # check disk count
    def checkDiskCount(self,diskCount,cloud):
        scanSiteList = self.siteMapper.getCloud(cloud)['sites']
        # loop over all sites
        for tmpSiteName in scanSiteList:
            if 'test' in tmpSiteName.lower():
                continue
            # get sitespec
            tmpSiteSpec = self.siteMapper.getSite(tmpSiteName)
            # use online only
            if not tmpSiteSpec.status in ['online']:
                continue
            # no size limit
            if tmpSiteSpec.maxinputsize in [0,None,'']:
                return True
            # enough space for input
            if int(tmpSiteSpec.maxinputsize) >= int(diskCount):
                return True
        # no sites have enough space
        return False


    # get available space
    def getAvailableSpace(self,space,fullRW,expRW):
        # calculate available space = totalT1space - ((RW(cloud)+RW(thistask))*GBperSI2kday))
        sizeCloud = fullRW * 0.2
        sizeThis  = expRW * 0.2
        aveSpace = space - (sizeCloud + sizeThis)
        return aveSpace,sizeCloud,sizeThis


    # make subscription
    def makeSubscription(self,dsCloudMap,RWs,fullRWs,expRWs,noEmptyCheck=False,acceptInProcess=False):
        nDDMtry = 3
        cloudList = []
        # collect clouds which don't hold datasets
        message = '%s possible clouds : %s' % (self.taskID,str(self.cloudForSubs))
        _logger.debug(message)
        for tmpDS,tmpClouds in dsCloudMap.iteritems():
            for tmpCloud in tmpClouds:
                if (not tmpCloud in cloudList) and tmpCloud in self.cloudForSubs:
                    cloudList.append(tmpCloud)
        message = '%s candidates for subscription : %s' % (self.taskID,str(cloudList))
        _logger.debug(message)
        self.sendMesg(message)
        if cloudList == []:
            _logger.debug('%s no candidates for subscription' % self.taskID)            
            return False
        # get DN
        com = 'unset LD_LIBRARY_PATH; unset PYTHONPATH; export PATH=/usr/local/bin:/bin:/usr/bin; '
        com+= 'source %s; grid-proxy-info -subject' % panda_config.glite_source
        status,DN = commands.getstatusoutput(com)
        _logger.debug('%s %s' % (self.taskID,DN))
        # ignore AC issuer
        if re.search('WARNING: Unable to verify signature!',DN) != None:
            status = 0
        if status != 0:
            _logger.error('%s could not get DN %s:%s' % (self.taskID,status,DN))
            return False
        # check if there is in-process subscription
        if not acceptInProcess:
            # remove /CN=proxy and /CN=limited from DN
            DN = DN.split('\n')[-1]
            DN = re.sub('(/CN=proxy)+$','',DN)
            DN = re.sub('/CN=limited proxy','',DN)
            status,out = dq2Common.parse_dn(DN)
            if status != 0:
                _logger.error('%s could not truncate DN %s:%s' % (self.taskID,status,DN))
                return False
            DN = out
            # loop over all datasets
            runningSub = {}
            for tmpDS,tmpClouds in dsCloudMap.iteritems():
                # get running subscriptions
                runningSub[tmpDS] = []
                _logger.debug('%s listSubscriptions(%s)' % (self.taskID,tmpDS))
                iTry = 0
                while True:
                    status,outLoc = ddm.DQ2.listSubscriptions(tmpDS)
                    # succeed
                    if status == 0:
                        break
                    # failed
                    iTry += 1
                    if iTry < nDDMtry:
                        time.sleep(30)
                    else:
                        _logger.error('%s %s' % (self.taskID,outLoc))
                        return False
                _logger.debug('%s %s %s' % (self.taskID,status,outLoc))                
                time.sleep(1)
                # get subscription metadata
                exec "outLoc = %s" % outLoc
                for tmpLocation in outLoc:
                    t1Flag = False
                    # check T1 or not
                    for tmpCloudName4T1 in self.siteMapper.getCloudList():
                        if tmpLocation in self.siteMapper.getCloud(tmpCloudName4T1)['tier1SE']:
                            t1Flag = True
                            break
                    # skip non-T1
                    if not t1Flag:
                        continue
                    _logger.debug('%s listSubscriptionInfo(%s,%s)' % (self.taskID,tmpDS,tmpLocation))
                    iTry = 0
                    while True:
                        status,outMeta = ddm.DQ2.listSubscriptionInfo(tmpDS,tmpLocation,0)
                        # succeed
                        if status == 0:
                            break
                        # skip non-existing ID
                        if re.search('not a Tiers of Atlas Destination',outMeta) != None:
                            _logger.debug('%s ignore %s' % (self.taskID,outMeta.split('\n')[-1]))
                            status = 0
                            outMeta = "()"
                            break
                        # failed
                        iTry += 1
                        if iTry < nDDMtry:
                            time.sleep(30)
                        else:
                            _logger.error('%s %s' % (self.taskID,outMeta))
                            return False
                    _logger.debug('%s %s %s' % (self.taskID,status,outMeta))
                    time.sleep(1)                
                    # look for DN in metadata
                    exec "outMeta = %s" % outMeta
                    if DN in outMeta:
                        # get corrosponding cloud
                        for tmpCloudName in self.siteMapper.getCloudList():
                            tmpCloudSpec = self.siteMapper.getCloud(tmpCloudName)
                            if tmpLocation in tmpCloudSpec['tier1SE']:
                                # append
                                if not tmpCloudName in runningSub[tmpDS]:
                                    runningSub[tmpDS].append(tmpCloudName)
                                break
            _logger.debug('%s runningSub=%s' % (self.taskID,runningSub))
            # doesn't make subscriptions when another subscriptions is in process
            subThr = 1
            for tmpDS,tmpClouds in runningSub.iteritems():
                if len(tmpClouds) > 0:
                    message = '%s subscription:%s to %s in process' % (self.taskID,tmpDS,str(tmpClouds))
                    _logger.debug(message)
                    self.sendMesg(message)
                    return False
        # get size of datasets
        dsSizeMap = {}
        for tmpDS in dsCloudMap.keys():
            _logger.debug('%s listFilesInDataset(%s)' % (self.taskID,tmpDS))
            iTry = 0
            while True:
                status,outList = ddm.DQ2.listFilesInDataset(tmpDS)
                # succeed
                if status == 0:
                    break
                # failed
                iTry += 1
                if iTry < nDDMtry:
                    time.sleep(30)
                else:
                    _logger.error('%s %s %s' % (self.taskID,status,outList))
                    return False
            # get total size
            dsSizeMap[tmpDS] = 0
            exec "outList = %s" % outList
            for guid,vals in outList[0].iteritems():
                try:
                    dsSizeMap[tmpDS] += long(vals['filesize'])
                except:
                    pass
            # GB
            _logger.debug('%s %s %sB' % (self.taskID,tmpDS,dsSizeMap[tmpDS]))
            dsSizeMap[tmpDS] /= (1024*1024*1024)
        _logger.debug('%s dsSize=%s' % (self.taskID,dsSizeMap))
        # check space and RW
        minRW    = None
        minCloud = None
        for tmpCloudName in cloudList:
            # get cloud spec
            tmpCloudSpec = self.siteMapper.getCloud(tmpCloudName)
            # get T1 site
            tmpT1Site = self.siteMapper.getSite(tmpCloudSpec['source'])
            # calculate available space
            if not fullRWs.has_key(tmpCloudName):
                fullRWs[tmpCloudName] = 0
            aveSpace,sizeCloud,sizeThis = self.getAvailableSpace(tmpT1Site.space,
                                                                 fullRWs[tmpCloudName],
                                                                 expRWs[self.taskID])
            # reduce requred space
            for tmpDS,tmpClouds in dsCloudMap.iteritems():
                if tmpCloudName in tmpClouds:
                    aveSpace -= dsSizeMap[tmpDS]
            # check space
            if aveSpace < thr_space_low:
                message = '%s    %s skip : space==%s total==%s' % (self.taskID,tmpCloudName,aveSpace,
                                                                   tmpT1Site.space)
                _logger.debug(message)
                self.sendMesg(message,msgType='warning')                
                continue
            _logger.debug('%s    %s pass : space==%s total==%s' % (self.taskID,tmpCloudName,aveSpace,
                                                                   tmpT1Site.space))
            # get cloud spec
            tmpCloudSpec = self.siteMapper.getCloud(tmpCloudName)
            # check MC share
            if tmpCloudSpec['mcshare'] == 0:
                message = '%s    %s skip : mcshare==%s' % (self.taskID,tmpCloudName,tmpCloudSpec['mcshare'])
                _logger.debug(message)
                continue
            # get minimum RW
            if not RWs.has_key(tmpCloudName):
                RWs[tmpCloudName] = 0
            tmpRwThr = tmpCloudSpec['mcshare']*thr_RW_sub    
            _logger.debug('%s    %s RW==%s Thr==%s' % (self.taskID,tmpCloudName,RWs[tmpCloudName],
                                                       tmpRwThr))
            tmpRwRatio = float(RWs[tmpCloudName])/float(tmpRwThr)
            if minRW == None or minRW > tmpRwRatio:
                minRW    = tmpRwRatio
                minCloud = tmpCloudName
        # check RW
        if minCloud == None:
            message = '%s no candidates left for subscription' % self.taskID
            _logger.debug(message)
            self.sendMesg(message)
            return False
        # get cloud spec
        tmpCloudSpec = self.siteMapper.getCloud(minCloud)
        # check threshold
        if minRW > 1.0 and not noEmptyCheck:
            message = '%s no empty cloud : %s minRW==%s>%s' % \
                      (self.taskID,minCloud,RWs[minCloud],thr_RW_sub*tmpCloudSpec['mcshare'])
            _logger.debug(message)
            self.sendMesg(message)
            return False
        message = '%s %s for subscription : minRW==%s' % (self.taskID,minCloud,minRW)
        _logger.debug(message)
        self.sendMesg(message)
        # get cloud spec for subscription
        tmpCloudSpec = self.siteMapper.getCloud(minCloud)
        # get T1 site
        tmpT1Site = self.siteMapper.getSite(tmpCloudSpec['source'])
        # dest DQ2 ID
        dq2ID = tmpT1Site.ddm
        # make subscription
        for tmpDS,tmpClouds in dsCloudMap.iteritems():
            # skip if the dataset already exists in the cloud
            if not minCloud in tmpClouds:
                _logger.debug('%s %s already exists in %s' % (self.taskID,tmpDS,minCloud))
                continue
            # register subscription
            optSrcPolicy = 001000 | 010000
            _logger.debug("%s %s %s" % ('registerDatasetSubscription',(tmpDS,dq2ID),
                                        {'version':0,'archived':0,'callbacks':{},'sources':{},
                                         'sources_policy':optSrcPolicy,'wait_for_sources':0,
                                         'destination':None,'query_more_sources':0,'sshare':"production",
                                         'group':None,'activity':None,'acl_alias':'secondary'}))
            iTry = 0
            while True:
                # execute
                status,out = ddm.DQ2.main('registerDatasetSubscription',tmpDS,dq2ID,version=0,archived=0,callbacks={},
                                          sources={},sources_policy=optSrcPolicy,wait_for_sources=0,destination=None,
                                          query_more_sources=0,sshare="production",group=None,activity=None,
                                          acl_alias='secondary')
                # succeed
                if status == 0 or 'DQSubscriptionExistsException' in out:
                    break
                # failed
                iTry += 1
                if iTry < nDDMtry:
                    time.sleep(30)
                else:
                    _logger.error('%s %s %s' % (self.taskID,status,out))
                    return False
            if 'DQSubscriptionExistsException' in out:    
                _logger.debug('%s %s %s' % (self.taskID,status,'DQSubscriptionExistsException'))
            else:
                _logger.debug('%s %s %s' % (self.taskID,status,out))                
            message = '%s registered %s %s:%s' % (self.taskID,tmpDS,minCloud,dq2ID)
            _logger.debug(message)
            self.sendMesg(message)
            time.sleep(1)
        # completed
        return True
