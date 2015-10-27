#!/usr/bin/python

# -*- mode: python; tab-width: 4; indent-tabs-mode: nil -*-

# DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS FILE HEADER.
# Copyright 2015 Palo Alto Research Center, Inc. (PARC), a Xerox company.  All Rights Reserved.
# The content of this file, whole or in part, is subject to licensing terms.
# If distributing this software, include this License Header Notice in each
# file and provide the accompanying LICENSE file.

# @author Christopher A. Wood, System Sciences Laboratory, PARC
# @copyright 2015 Palo Alto Research Center, Inc. (PARC), A Xerox Company. All Rights Reserved.

from __future__ import with_statement

import os
import sys
import errno
import argparse
import time
import tempfile
import json
import stat

from fuse import FUSE, FuseOSError, Operations

sys.path.append('/Users/cwood/PARC/Distillery/build/lib/python2.7/site-packages')
from CCNx import *

class CCNxClient(object):
    def __init__(self, async = False):
        self.portal = self.openAsyncPortal() if async else self.openPortal()

    def setupIdentity(self):
        global IDENTITY_FILE
        IDENTITY_FILE = tempfile.NamedTemporaryFile(suffix=".p12")
        identity = create_pkcs12_keystore(IDENTITY_FILE.name, "foobar", "bletch", 1024, 10)
        return identity

    def openPortal(self):
        identity = self.setupIdentity()
        factory = PortalFactory(identity)
        portal = factory.create_portal()
        return portal

    def openAsyncPortal(self):
        identity = self.setupIdentity()
        factory = PortalFactory(identity)
        portal = factory.create_portal(transport=TransportType_RTA_Message, attributes=PortalAttributes_NonBlocking)
        return portal

    def get(self, name, data):
        interest = Interest(Name(name))
        if data != None:
            interest.setPayload(data)

        self.portal.send(interest)
        response = self.portal.receive()

        if isinstance(response, ContentObject):
            return response.getPayload()
        else:
            return None

    def get_async(self, name, data, timeout_seconds):
        interest = None
        if data == None:
            interest = Interest(Name(name))
        else:
            interest = Interest(Name(name), payload=data)

        for i in range(timeout_seconds):
            try:
                self.portal.send(interest)
                response = self.portal.receive()
                if response and isinstance(response, ContentObject):
                    return response.getPayload()
            except Portal.CommunicationsError as x:
                if x.errno == errno.EAGAIN:
                    time.sleep(1)
                else:
                    raise
        return None

    def push(self, name, data):
        interest = Interest(Name(name), payload=data)
        try:
            self.portal.send(interest)
        except Portal.CommunicationsError as x:
            sys.stderr.write("ccnxPortal_Write failed: %d\n" % (x.errno,))
        pass

    def listen(self, prefix):
        try:
            self.portal.listen(Name(prefix))
        except Portal.CommunicationsError as x:
            sys.stderr.write("CCNxClient: comm error attempting to listen: %s\n" % (x.errno,))
        return True

    def receive(self):
        request = self.portal.receive()
        if isinstance(request, Interest):
            return str(request.name), request.getPayload()
        else:
            pass
        return None, None

    def receive_raw(self):
        request = self.portal.receive()
        if isinstance(request, Interest):
            return request.name, request.getPayload()
        else:
            pass
        return None, None

    def reply(self, name, data):
        try:
            self.portal.send(ContentObject(Name(name), data))
        except Portal.CommunicationsError as x:
            sys.stderr.write("reply failed: %d\n" % (x.errno,))

class FileHandle(Object):
    access = False
    mode = stat.S_READ
    uid = 0
    gid = 0

    def __init__(self, name, fid):
        self.name = name
        self.fid = dif
        self.offset = 0
        self.size = 0
        self.access = False
        self.mode = stat.S_READ
        self.uid = 0
        self.gid = 0

    def load(self):
        pass

    def read(self):
        pass

    def write(self):
        pass

class LocalFileHandle(FileHandle):
    def __init__(self, name, fid, data):
        super(LocalFileHandle, self).__init__(name, fid)

    def load(self):
        with open(self.name) as fhandle:
            self.data = fhandle.read()
            self.size = len(data)
        return self

    def read(self, length, offset):
        max_offset = max(self.size - 1, offset + length)
        if offset >= self.size:
            return None
        else:
            return self.data[offset:max_offset]

    def write(self):
        pass

class RemoteFileHandle(FileHandle):
    def __init__(self, name, fid, client):
        super(LocalFileHandle, self).__init__(name, fid)
        self.client = client

    def load(self):
        self.data = self.client.get(name)
        # TODO: need to adjust the client to retrieve the data lifetime so
        # we can refresh the local copy if needed
        if data != None:
            self.size = len(data)
        return self

    def read(self):
        max_offset = max(self.size - 1, offset + length)
        if offset >= self.size:
            return None
        else:
            return self.data[offset:max_offset]

    def write(self):
        pass

class ContentStore(Object):
    def __init__(self, root):
        self.root = root
        self.files = {}
        self.handles = {}
        self.descriptor_seq = 0

    def contains_file(self, name):
        return name in self.files

    def access(self, name):
        return self.files[name].access()

    def chmod(self, name, mode):
        return self.files[name].mode = mode

    def chown(self, name, uid, gid):
        self.files[name].uid = uid
        self.files[name].uid = gid
        return True

    def load(self, name):
        return self.files[name].load()

    def open(self, name):
        if self.contains_file(name):
            return self.load(name).fid
        else:
            return self.create_remote_file(name).load().fid

    def get_handle(self, fh):
        if fh in self.handles:
            return self.handles[fh]
        else:
            return None # TODO: throw an exception here

    def create_local_file(self, name):
        if name not in self.files:
            self.files[name] = LocalFileHandle(name, descriptor_seq)
            self.handles[descriptor_seq] = self.files[name]
            descriptor_seq += 1
        return self.files[name]

    def create_remote_file(self, name):
        if name not in self.files:
            self.files[name] = RemoteFileHandle(name, descriptor_seq)
            self.handles[descriptor_seq] = self.files[name]
            descriptor_seq += 1
        return self.files[name]

class CCNxDrive(Operations):
    def __init__(self, root):
        self.root = root
        self.client = CCNxClient()
        self.content_store = ContentStore(root)

    # def _full_path(self, partial):
    #     if partial.startswith("/"):
    #         partial = partial[1:]
    #     path = os.path.join(self.root, partial)
    #     return path

    def access(self, path, mode):
        ''' Return True if access is allowed, and False otherwise.
        '''
        return self.content_store.access(path)

    def chmod(self, path, mode):
        ''' ???
        '''
        return os.chmod(path, mode)

    def chown(self, path, uid, gid):
        ''' ???
        '''
        return os.chown(path, uid, gid)

    def getattr(self, path, fh=None):
        full_path = self._full_path(path)
        st = os.lstat(full_path)
        return dict((key, getattr(st, key)) for key in ('st_atime', 'st_ctime',
                     'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))

    def readdir(self, path, fh):
        full_path = self._full_path(path)

        dirents = ['.', '..']
        if os.path.isdir(full_path):
            dirents.extend(os.listdir(full_path))
        for r in dirents:
            yield r

    def readlink(self, path):
        pathname = os.readlink(self._full_path(path))
        if pathname.startswith("/"):
            # Path name is absolute, sanitize it.
            return os.path.relpath(pathname, self.root)
        else:
            return pathname

    def mknod(self, path, mode, dev):
        return os.mknod(self._full_path(path), mode, dev)

    def rmdir(self, path):
        full_path = self._full_path(path)
        return os.rmdir(full_path)

    def mkdir(self, path, mode):
        return os.mkdir(self._full_path(path), mode)

    def statfs(self, path):
        full_path = self._full_path(path)
        stv = os.statvfs(full_path)
        return dict((key, getattr(stv, key)) for key in ('f_bavail', 'f_bfree',
            'f_blocks', 'f_bsize', 'f_favail', 'f_ffree', 'f_files', 'f_flag',
            'f_frsize', 'f_namemax'))

    def unlink(self, path):
        return os.unlink(self._full_path(path))

    def symlink(self, name, target):
        return os.symlink(name, self._full_path(target))

    def rename(self, old, new):
        return os.rename(self._full_path(old), self._full_path(new))

    def link(self, target, name):
        return os.link(self._full_path(target), self._full_path(name))

    def utimens(self, path, times=None):
        return os.utime(self._full_path(path), times)

    def open(self, path, flags):
        # TODO: what about flags?
        return self.content_store.open(path)

    def create(self, path, mode, fi=None):
        # TODO: what about flags? os.O_WRONLY | os.O_CREAT
        # TODO: what about mode?
        return self.content_store.create_local_file(path).fid

    def read(self, path, length, offset, fh):
        handle = self.content_store.get_handle(fh)
        return handle.read(length, offset)

    def write(self, path, buffer, offset, fh):
        handle = self.content_store.get_handle(fh)
        return handle.write(buffer, offset)

    ## TODO: ???
    def truncate(self, path, length, fh=None):
        full_path = self._full_path(path)
        with open(full_path, 'r+') as f:
            f.truncate(length)

    ## TODO: ???
    def flush(self, path, fh):
        return os.fsync(fh)

    ## TODO: ???
    def release(self, path, fh):
        return os.close(fh)

    ## TODO: ???
    def fsync(self, path, fdatasync, fh):
        return self.flush(path, fh)

def main(mountpoint, root):
    FUSE(CCNxDrive(root), mountpoint, nothreads=True, foreground=True)

if __name__ == '__main__':
    desc = '''CCN-FUSE: The FUSE adapter for CCN.
    '''

    parser = argparse.ArgumentParser(prog='ccn-fuse', formatter_class=argparse.RawDescriptionHelpFormatter, description=desc)
    parser.add_argument('-m', '--mount', action="store", required=True, help="The CCN-FUSE moint point.")
    parser.add_argument('-r', '--root', action="store", required=True, help="The root of the CCN-FUSE file system.")

    args = parser.parse_args()

    main(args.mount, args.root)
