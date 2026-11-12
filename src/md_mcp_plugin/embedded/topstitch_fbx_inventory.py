"""Inspect authored mesh normals and transform records in a small binary FBX.

Reports on disk data only. It does not evaluate FBX transforms, generate
missing normals, or verify a native Marvelous Designer pose.
"""

from hashlib import sha256
import math
import struct
import zlib


MAGIC = b'Kaydara FBX Binary  \x00\x1a\x00'
MAX_BYTES = 16 * 1024 * 1024
SCALARS = {'C': 'B', 'B': 'B', 'Y': 'h', 'I': 'i', 'F': 'f', 'L': 'q', 'D': 'd'}
ARRAYS = {'c': 'B', 'b': 'B', 'i': 'i', 'l': 'q', 'f': 'f', 'd': 'd'}
TRANSFORM_PROPERTIES = {
    'Lcl Translation', 'Lcl Rotation', 'Lcl Scaling', 'RotationOrder', 'RotationActive',
    'PreRotation', 'PostRotation', 'RotationOffset', 'RotationPivot', 'ScalingOffset', 'ScalingPivot',
    'GeometricTranslation', 'GeometricRotation', 'GeometricScaling', 'InheritType',
}
AXIS_PROPERTIES = {'UpAxis', 'UpAxisSign', 'FrontAxis', 'FrontAxisSign', 'CoordAxis', 'CoordAxisSign', 'UnitScaleFactor'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


class BinaryFbx:
    def __init__(self, data):
        require(MAGIC == data[:len(MAGIC)] and 27 <= len(data) <= MAX_BYTES, 'Expected a bounded little-endian binary FBX')
        self.data = data
        self.version, = struct.unpack_from('<I', data, len(MAGIC))
        require(7100 <= self.version <= 7700, 'Expected FBX version 7100 through 7700')
        self.header = struct.Struct('<QQQB' if self.version >= 7500 else '<IIIB')

    def nodes(self, start, limit):
        while start < limit:
            require(start + self.header.size <= limit, 'Truncated FBX record header')
            end, count, size, name_size = self.header.unpack_from(self.data, start)
            name_start = start + self.header.size
            if end == count == size == name_size == 0:
                return
            props = name_start + name_size
            children = props + size
            require(0 < name_size and props <= children <= end <= limit, 'Invalid FBX record offsets')
            yield self.data[name_start:props].decode('ascii'), (props, children, count), (children, end)
            start = end

    def props(self, start, limit, count):
        def take(size):
            nonlocal start
            end = start + size
            require(end <= limit, 'Truncated FBX property')
            value = self.data[start:end]
            start = end
            return value

        require(count <= limit - start and count <= 128, 'Unexpected FBX property count')
        values = []
        for _ in range(count):
            kind = take(1).decode('ascii')
            if kind in SCALARS:
                fmt = '<' + SCALARS[kind]
                values.append(struct.unpack(fmt, take(struct.calcsize(fmt)))[0])
            elif kind in ('S', 'R'):
                size, = struct.unpack('<I', take(4))
                raw = take(size)
                values.append(raw.decode('utf-8') if kind == 'S' else raw)
            else:
                require(kind in ARRAYS, 'Unknown FBX property type')
                items, encoding, size = struct.unpack('<III', take(12))
                values.append((kind, items, encoding, take(size)))
        require(start == limit, 'FBX property bytes do not match the declared size')
        return values

    def children(self, node):
        return list(self.nodes(*node[2]))

    def settings(self, node, names):
        values = {}
        for group in self.children(node):
            if group[0] != 'Properties70':
                continue
            for child in self.children(group):
                if child[0] != 'P':
                    continue
                props = self.props(*child[1])
                if props[0] in names:
                    values[props[0]] = props[4:]
        return values


def read_array(prop, allowed):
    kind, count, encoding, data = prop
    require(kind in allowed, 'Unexpected FBX array element type')
    fmt = '<' + ARRAYS[kind]
    expected = count * struct.calcsize(fmt)
    require(expected <= MAX_BYTES and encoding in (0, 1), 'Unexpected FBX array bounds or encoding')
    if encoding == 1:
        decoder = zlib.decompressobj()
        data = decoder.decompress(data, expected + 1)
        require(decoder.eof and not decoder.unused_data, 'FBX compressed array does not match its declared bounds')
    require(len(data) == expected, 'FBX array bytes do not match the declared length')
    values = [item[0] for item in struct.iter_unpack(fmt, data)]
    require(all(map(math.isfinite, values)), 'FBX array contains non-finite data')
    return {'storage': kind, 'count': count, 'sha256': sha256(data).hexdigest()}, values


def mesh_inventory(fbx, node):
    children = fbx.children(node)
    named = {n[0]: n for n in children}

    def array(name, allowed):
        prop, = fbx.props(*named[name][1])
        return read_array(prop, allowed)

    vertices, _ = array('Vertices', 'df')
    polygon_indices, indices = array('PolygonVertexIndex', 'il')
    vertex_count = vertices['count'] // 3
    polygon_count = sum(index < 0 for index in indices)
    require(vertices['count'] % 3 == 0 and bool(indices) and indices[-1] < 0
            and all(0 <= (index if index >= 0 else -index - 1) < vertex_count for index in indices),
            'Invalid FBX polygon indices or vertex count')
    layers = []
    for child in children:
        if child[0] != 'LayerElementNormal':
            continue
        fields = {n[0]: fbx.props(*n[1])[0] for n in fbx.children(child) if n[0] in (
            'MappingInformationType', 'ReferenceInformationType', 'Normals', 'NormalsIndex')}
        normals, _ = read_array(fields['Normals'], 'df')
        mapping, reference = fields['MappingInformationType'], fields['ReferenceInformationType']
        expected = {'ByPolygonVertex': len(indices), 'ByVertice': vertex_count,
                    'ByVertex': vertex_count, 'ByPolygon': polygon_count, 'AllSame': 1}.get(mapping)
        normal_count = normals['count'] // 3
        index_storage, normal_indices = read_array(fields['NormalsIndex'], 'il') if 'NormalsIndex' in fields else (None, [])
        valid_mapping = (reference == 'Direct' and normal_count == expected) or (
            reference in ('Index', 'IndexToDirect') and index_storage is not None and len(normal_indices) == expected
            and all(0 <= index < normal_count for index in normal_indices))
        layers.append({'mapping': mapping, 'reference': reference, 'normals': normals, 'normal_indices': index_storage,
                       'normal_count': normal_count, 'layout_valid': normals['count'] % 3 == 0 and valid_mapping})
    return {'vertex_count': vertex_count, 'polygon_count': polygon_count, 'vertices': vertices,
            'polygon_indices': polygon_indices, 'normal_layers': layers}


def inspect_fbx(data):
    fbx = BinaryFbx(data)
    top = {node[0]: node for node in fbx.nodes(27, len(data))}
    meshes, models, parent_ids = {}, {}, {}
    for node in fbx.children(top['Objects']):
        if node[0] not in ('Geometry', 'Model'):
            continue
        obj_id, name, subtype = fbx.props(*node[1])
        name = name.split('\x00\x01', 1)[0]
        if node[0] == 'Geometry' and subtype == 'Mesh':
            meshes[obj_id] = {'name': name, **mesh_inventory(fbx, node)}
        elif node[0] == 'Model':
            models[obj_id] = {'name': name, 'type': subtype, 'authored_transform_properties': fbx.settings(node, TRANSFORM_PROPERTIES)}
    for node in fbx.children(top['Connections']):
        if node[0] != 'C':
            continue
        link, child_id, parent_id, *_ = fbx.props(*node[1])
        if link == 'OO' and (child_id in meshes or child_id in models) and (parent_id == 0 or parent_id in models):
            parent_ids.setdefault(child_id, []).append(parent_id)
    for obj_id, obj in (meshes | models).items():
        obj['parent_model_ids'] = parent_ids.get(obj_id, [])
    axis = fbx.settings(top['GlobalSettings'], AXIS_PROPERTIES) if 'GlobalSettings' in top else {}
    return {'fbx_version': fbx.version, 'file_sha256': sha256(data).hexdigest(), 'axis_and_unit_properties': axis,
            'meshes': meshes, 'models': models}
