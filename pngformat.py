# coding: utf-8

import struct
import binascii
import base64
import zlib
import io
from collections import OrderedDict, deque
import operator


PNG_HEAD = b'\x89\x50\x4E\x47\x0D\x0A\x1A\x0A'

PNG_IHDR = 'IHDR'
PNG_PLTE = 'PLTE'
PNG_IDAT = 'IDAT'
PNG_IEND = 'IEND'

bstr = binascii.hexlify

TEST_FP = 'pngImages/test1.png'


def sync_io(fd1, fd2):
    fd2.seek(fd1.tell(), 0)


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
        bit_depth = self.ihdr_dict.bit_depth
        color_type = CodesColorType(self.ihdr_dict.color_type)

        pix_keys = []
        pix_frmt = '>'
        if color_type.uses_palette:
            pix_keys.append('pi')
            if bit_depth == 1:
                raise NotImplementedError('Have yet to implement 1-bit stuff')
            elif bit_depth == 2:
                pix_frmt += 'B'
            elif bit_depth == 4:
                pix_frmt += 'H'
            elif bit_depth == 8:
                pix_frmt += 'I'
            else:
                raise ValueError('Illegal bit-depth for this color type')
        else:
            if color_type.uses_color:
                pix_keys.extend(('r', 'g', 'b'))

                if bit_depth == 8:
                    pix_frmt += 'BBB'

                    if color_type.uses_alpha:
                        pix_frmt += 'B'
                    else:
                        pix_frmt += 'x'

                elif bit_depth == 16:
                    pix_frmt += 'HHH'
                    if color_type.uses_alpha:
                        pix_frmt += 'H'
                    else:
                        pix_frmt += 'xx'
                else:
                    raise ValueError('Illegal bit-depth for this color type')
            else:
                pix_keys.append('v')

                if bit_depth == 1:
                    raise NotImplementedError('Have yet to implement 1-bit stuff')

                elif bit_depth == 2:
                    pix_frmt += 'B'

                elif bit_depth == 4:
                    pix_frmt += 'H'

                elif bit_depth == 8:
                    if color_type.uses_alpha:
                        pix_frmt += 'H'
                    else:
                        pix_frmt += 'I'

                elif bit_depth == 16:
                    if color_type.uses_alpha:
                        pix_frmt += 'I'
                    else:
                        pix_frmt += 'Q'

                else:
                    raise ValueError('Illegal bit-depth for this color type')

            if color_type.uses_alpha:
                pix_keys.append('a')

        data_buf = io.BytesIO(pix_data)
        data_buf_zero = io.BytesIO()
        data_buf_filtered = io.BytesIO()
        all_pix_len = len(pix_data)
        pix_index = 0
        scan_lines = 0
        line_filter = CodesFilterTypes(0)  # Filter type "None"
        struct_filter_byte = struct.Struct('>B')
        one_pix_frmt = struct.Struct(pix_frmt)
        one_pix_len = one_pix_frmt.size

        pix_bytes_frmt_parts = [struct.Struct('>' + x) for x in pix_frmt[1:]]
        pix_bytes_frmt_parts_sizes = [x.size for x in pix_bytes_frmt_parts]

        prev_sl_deque = deque()
        curr_sl_deque = deque()

        for x in xrange(width * one_pix_len):
            data_buf_zero.write('\x00')

        data_buf_zero.seek(0)
        for x in xrange(width):
            curr_sl_deque.append(
                [
                    pix_bytes_frmt_parts[i].unpack(
                        data_buf_zero.read(pix_bytes_frmt_parts_sizes[i])
                    )[0]
                    for i in xrange(len(pix_bytes_frmt_parts_sizes))
                ])

        while data_buf.tell() < all_pix_len:
            # if pix_index % width == 0:  # This is a filter byte
            if len(curr_sl_deque) == width:
                if pix_index != 0:
                    for pix in curr_sl_deque:  # Dump the previous scanline
                        for i in xrange(len(pix_bytes_frmt_parts_sizes)):
                            try:
                                r2 = pix_bytes_frmt_parts[i].pack(pix[i])
                            except struct.error:
                                r2 = pix_bytes_frmt_parts[i].pack(0)
                            data_buf_filtered.write(r2)

                prev_sl_deque = curr_sl_deque
                curr_sl_deque = deque()

                line_filter = CodesFilterTypes(struct_filter_byte.unpack(data_buf.read(1))[0])
                data_buf_filtered.write(struct_filter_byte.pack(line_filter))
            else:
                pix_byte_pos = data_buf.tell()
                next_pix = []
                for i in xrange(len(pix_bytes_frmt_parts_sizes)):
                    pb2 = data_buf.read(pix_bytes_frmt_parts_sizes[i])
                    pbr = pix_bytes_frmt_parts[i].unpack(pb2)
                    next_pix.append(pbr[0])
                curr_sl_deque.append(next_pix)

                # pix_bin_raw = data_buf.read(one_pix_len)
                # pix_byte_raw = one_pix_frmt.unpack(pix_bin_raw)

                if line_filter.name == 'None':
                    # data_buf_filtered.write(pix_bin_raw)
                    pass

                elif line_filter.name == 'Sub':

                    if len(curr_sl_deque) == 1:
                        other_pix_byte_tup = prev_sl_deque[-1]
                    else:
                        other_pix_byte_tup = curr_sl_deque[-2]

                    cur_val = curr_sl_deque.pop()
                    result_tup = map(operator.add, cur_val, other_pix_byte_tup)
                    #result_tup = map(operator.abs, result_tup)
                    curr_sl_deque.append(result_tup)

                elif line_filter.name == 'Up':
                    other_pix_byte_tup = prev_sl_deque[len(curr_sl_deque) - 1]
                    cur_val = curr_sl_deque.pop()
                    result_tup = map(operator.add, cur_val, other_pix_byte_tup)
                    curr_sl_deque.append(result_tup)

                elif line_filter.name == 'Average':
                    if len(curr_sl_deque) == 1:
                        other_left = prev_sl_deque[-1]

                    else:
                        other_left = curr_sl_deque[-2]

                    other_above = prev_sl_deque[len(curr_sl_deque) - 1]

                    other_sum = map(operator.add, other_left, other_above)
                    other_av = map(operator.floordiv, other_sum, 2)

                    result_tup = map(operator.add, curr_sl_deque.pop(), other_av)
                    curr_sl_deque.append(result_tup)

                elif line_filter.name == 'Paeth':
                    # TODO: finish
                    pass
                else:
                    raise NotImplementedError('Have yet to implement filter :' + line_filter.name)
            pix_index += 1

        for pix in curr_sl_deque:  # Dump the previous scanline
            for i in xrange(len(pix_bytes_frmt_parts_sizes)):
                data_buf_filtered.write(pix_bytes_frmt_parts[i].pack(pix[i]))

        pix_index = 0

        filtered_len = data_buf_filtered.tell()

        data_buf_filtered.seek(0)

        while data_buf_filtered.tell() < (filtered_len - one_pix_len):
            if pix_index % width == 0:  # This is a filter byte
                line_filter = CodesFilterTypes(struct_filter_byte.unpack(data_buf_filtered.read(1))[0])
                yield line_filter.name
            pix_byte_pos = data_buf_filtered.tell()
            pix_bin_filtered = data_buf_filtered.read(one_pix_len)

            pix_index += 1
            yield data_buf_filtered.tell(), bstr(pix_bin_filtered), dict(zip(pix_keys, one_pix_frmt.unpack(pix_bin_filtered)))

        data_buf.close()
        data_buf_zero.close()
        data_buf_filtered.close()


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


class CodesColorType(int, object):
    @property
    def uses_palette(self):
        return self == 3

    @property
    def uses_color(self):
        return self == 2 or self == 6

    @property
    def uses_alpha(self):
        return self == 4 or self == 6

    @property
    def as_dict(self):
        return {'palette': self.uses_palette, 'color': self.uses_color, 'alpha': self.uses_alpha}


class CodesFilterTypes(int, object):
    NONE = 0
    SUB = 1
    UP = 2
    AVERAGE = 3
    PAETH = 4

    @property
    def name(self):
        return ('None', 'Sub', 'Up', 'Average', 'Paeth')[self]

png1 = PngFileHandle.read_file(TEST_FP)
for ch in png1.chunks:
    if ch.chunk_type == PNG_IDAT:
        print ch.data_as_hex[:30]

print CodesFilterTypes.NONE

print bstr(png1.ihdr_dict.chunk_bytes)
print png1.ihdr_dict
print png1.ihdr_dict.crc
print CodesColorType(png1.ihdr_dict.color_type).as_dict

with open('dump1.txt', 'w') as fd2:
    for p1 in png1.get_pixels():
        fd2.write(repr(p1) + '\n')
print "hi"
