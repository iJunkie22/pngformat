# coding: utf-8

import struct
import binascii
import base64
import zlib
import io
from collections import OrderedDict


PNG_HEAD = b'\x89\x50\x4E\x47\x0D\x0A\x1A\x0A'

PNG_IHDR = 'IHDR'
PNG_PLTE = 'PLTE'
PNG_IDAT = 'IDAT'
PNG_IEND = 'IEND'

bstr = binascii.hexlify

TEST_FP = 'pngImages/test1.png'


class PngChunk(object):
    struct_header = struct.Struct('>i4s')

    def __init__(self, *args, **kwargs):
        self.clength = None
        self.chunk_type = None
        self.chunk_data = None
        self.crc = None

    @classmethod
    def read_new(cls, fd_in):
        new_chunk = cls()

        cheader = fd_in.read(8)
        if len(cheader) == 8:
            new_chunk.clength, new_chunk.chunk_type = cls.struct_header.unpack(cheader)
            new_chunk.chunk_data = fd_in.read(new_chunk.clength)
            new_chunk.crc = fd_in.read(4)
        return new_chunk

    @property
    def is_valid_type(self):
        return isinstance(self.chunk_type, (str, unicode)) and self.chunk_type[2].isupper()

    @property
    def is_critical(self):
        if self.is_valid_type:
            return self.chunk_type[0].isupper()
        else:
            raise AttributeError('Not a valid chunk_type')

    @property
    def is_public(self):
        if self.is_valid_type:
            return self.chunk_type[1].isupper()
        else:
            raise AttributeError('Not a valid chunk_type')

    @property
    def is_copy_safe(self):
        if self.is_valid_type:
            return self.chunk_type[3].islower()
        else:
            raise AttributeError('Not a valid chunk_type')

    @property
    def data_as_hex(self):
        return binascii.hexlify(self.chunk_data)


class PngFileHandle(object):
    def __init__(self, *args, **kwargs):
        self.chunks = []

    @property
    def ihdr_block(self):
        assert len(self.chunks) > 0, 'No chunks found'
        chunk_x = self.chunks[0]
        assert isinstance(chunk_x, PngChunk)
        assert chunk_x.chunk_type == PNG_IHDR, 'Incorrect type code'
        return chunk_x

    @property
    def ihdr_dict(self):
        return IHDRDict.frombytes(self.ihdr_block.chunk_data)

    @property
    def width(self):
        return self.ihdr_dict.width

    @property
    def decompressed_data(self):
        concated_chunks = b''
        for ch1 in self.chunks:
            if ch1.chunk_type == PNG_IDAT:
                concated_chunks += ch1.chunk_data

        return zlib.decompress(concated_chunks, 0)

    @property
    def dec_data_as_hex(self):
        return binascii.hexlify(self.decompressed_data)

    @classmethod
    def read_file(cls, fp):
        new_png = cls()

        fd1 = open(fp, 'rb')

        try:
            header = fd1.read(len(PNG_HEAD))
            last_chunk = PngChunk()
            last_chunk.chunk_type = 'XXXX'
            while last_chunk.chunk_type != PNG_IEND:
                last_chunk = PngChunk.read_new(fd1)
                new_png.chunks.append(last_chunk)
        finally:
            fd1.close()
        return new_png

    def get_pixels(self):
        width = self.width
        pix_data = self.decompressed_data
        data_buf = io.BytesIO(pix_data)
        pix_len = len(pix_data)
        lines_pixls = 0
        while data_buf.tell() < pix_len:
            if lines_pixls % width == 0:
                line_bit = data_buf.read(1)
            pix_bin = data_buf.read(4)
            lines_pixls += 1
            yield data_buf.tell(), bstr(pix_bin), struct.unpack('>4B', pix_bin)

        data_buf.close()


class IHDRDict(OrderedDict, object):
    CHUNK_KEYS = ('Width', 'Height', 'Bit depth', 'Color type', 'Compression method',
                  'Filter method', 'Interlace method')

    @classmethod
    def frombytes(cls, bs):
        if len(bs) == 25:
            sformat = '>8x2i5b4x'
        elif len(bs) == 13:
            sformat = '>2i5b'
        else:
            raise RuntimeError('Invalid IHDR length')

        chunk_values = struct.unpack(sformat, bs)
        return cls(zip(cls.CHUNK_KEYS, chunk_values))

    @property
    def width(self):
        return self['Width']

    @property
    def height(self):
        return self['Height']

    @property
    def bit_depth(self):
        return self['Bit depth']

    @property
    def color_type(self):
        return self['Color type']

    @property
    def compression_method(self):
        return self['Compression method']

    @property
    def filter_method(self):
        return self['Filter method']

    @property
    def interlace_method(self):
        return self['Interlace method']

    @property
    def data_bytes(self):
        return struct.pack('>2i5b', *self.values())

    @property
    def crc(self):
        return zlib.crc32(struct.pack('>4s', 'IHDR') + self.data_bytes)

    @property
    def chunk_bytes(self):
        return struct.pack('>i4s', 25, 'IHDR') + self.data_bytes + struct.pack('>i', self.crc)


def read_chunk(fd_in):
    cheader = fd_in.read(8)
    if len(cheader) != 8:
        return {'Length': None, 'ChunkType': None, 'ChunkData': None, 'CRC': None}

    cl, ct = struct.unpack('>i4s', cheader)
    sdata = fd_in.read(cl)
    crc = fd_in.read(4)
    return {'Length': cl, 'ChunkType': ct, 'ChunkData': sdata, 'CRC': crc}


png1 = PngFileHandle.read_file(TEST_FP)
for ch in png1.chunks:
    if ch.chunk_type == PNG_IDAT:
        print ch.data_as_hex[:30]

print bstr(png1.ihdr_dict.chunk_bytes)
print png1.ihdr_dict.crc

with open('dump.txt', 'w') as fd2:
    for p1 in png1.get_pixels():
        fd2.write(repr(p1) + '\n')
print "hi"
