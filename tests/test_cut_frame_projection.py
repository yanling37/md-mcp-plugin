"""Offline PNG/native geometry only. No SDK, Qt, DLL or MD."""
import copy
import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from md_mcp_plugin.embedded import cut_frame_projection as p
from md_mcp_plugin.embedded import cut_qt_geometry as g


def png(points, scale=1):
    w, h = 240*scale, 220*scale
    pixels = bytearray([255, 255, 255, 255] * w*h)
    for cx, cy in points:
        for y in range(round(cy)-2*scale, round(cy)+2*scale+1):
            for x in range(round(cx)-2*scale, round(cx)+2*scale+1):
                i = 4*(y*w+x); pixels[i:i+4] = bytes([255, 230, 0, 255])
    def chunk(tag, data):
        return struct.pack('>I', len(data))+tag+data+struct.pack('>I', zlib.crc32(tag+data)&0xffffffff)
    raw = b''.join(b'\0'+pixels[y*w*4:(y+1)*w*4] for y in range(h))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')


POINTS = [(0, 0), (30, 0), (24, 37), (0, 21)]
def anchors(points=POINTS):
    return [{'id': str(i), 'xy_mm': list(x)} for i,x in enumerate(points)]


def native(points=POINTS):
    lines = []
    for i, (a,b) in enumerate(zip(points, points[1:]+points[:1])):
        lines.append({'ID': 'L'+str(i), 'PointList': [
            {'ID':'P'+str(i),'Position':{'x':a[0],'y':a[1]}},
            {'ID':'P'+str((i+1)%len(points)),'Position':{'x':b[0],'y':b[1]}}]})
    return {'PatternList':[{'ID':'owner','ShapeInfo':{'ID':'outline','IsClosed':True,'LineList':lines}}]}


class ProjectionContracts(unittest.TestCase):
    def test_unique_direct_full_bijection_no_dispatch(self):
        r=p.solve_anchor_only_projection(png([(20+3*x,30+3*y) for x,y in POINTS]),anchors())
        self.assertTrue(r['projection_geometry_verified'],r)
        self.assertFalse(r['reflected']); self.assertEqual(r['matrix_native_mm_to_logical'],[3,0,0,3,20,30])
        self.assertTrue(r['full_bijection']); self.assertEqual(r['maximum_residual_squared_logical_px'],'0')
        self.assertFalse(r['dispatch_authorized']); self.assertFalse(r['blue_used'])

    def test_unique_reflection_dpr(self):
        r=p.solve_anchor_only_projection(png([(40+6*x,350-6*y) for x,y in POINTS],2),anchors(),device_pixel_ratio=2)
        self.assertTrue(r['projection_geometry_verified'],r); self.assertTrue(r['reflected'])
        self.assertEqual(r['matrix_native_mm_to_logical'],[3,0,0,-3,20,175])

    def test_symmetric_assignment_is_not_semantic_evidence(self):
        square=[(0,0),(20,0),(20,20),(0,20)]
        r=p.solve_anchor_only_projection(png([(30+3*x,30+3*y) for x,y in square]),anchors(square))
        self.assertFalse(r['projection_geometry_verified']); self.assertEqual(r['reason'],'AMBIGUOUS_ANCHOR_SIMILARITY')

    def test_missing_extra_and_nonuniform_scale_reject(self):
        for points in ([(20+3*x,30+3*y) for x,y in POINTS][:-1],
                       [(20+3*x,30+3*y) for x,y in POINTS]+[(190,180)],
                       [(20+3*x,30+4*y) for x,y in POINTS]):
            self.assertFalse(p.solve_anchor_only_projection(png(points),anchors())['projection_geometry_verified'])

    def test_degenerate_and_typed_parameters_reject(self):
        for a in (anchors([(0,0),(1,0),(2,0)]), anchors()+[anchors()[0]]):
            self.assertFalse(p.solve_anchor_only_projection(png([]),a)['projection_geometry_verified'])
        self.assertFalse(p.solve_anchor_only_projection(png([]),anchors(),device_pixel_ratio=True)['projection_geometry_verified'])

    def test_outer_endpoint_ids_graph_and_aliases(self):
        a=p.outer_endpoint_anchors(native()); self.assertEqual(len(a),4)
        self.assertTrue(all(x['same_native_PointID'] for x in a))
        n=native(); n['PatternList'][0]['ShapeInfo']['LineList'][0]['PointList'][-1]['ID']='alias'
        self.assertFalse(all(x['same_native_PointID'] for x in p.outer_endpoint_anchors(n)))
        n=native(); n['PatternList'][0]['ShapeInfo']['LineList'][0]['PointList'][-1]['Position']['x']+=1
        with self.assertRaises(ValueError): p.outer_endpoint_anchors(n)
        n=native(); n['PatternList'][0]['ShapeInfo']['IsClosed']=False
        with self.assertRaises(ValueError): p.outer_endpoint_anchors(n)

    def test_candidate_is_conservative_geometry_only(self):
        n=native(); n['PatternList'][0]['InternalLineList']=[{'ID':'shape','ShapeType':1,'IsClosed':False,'LineList':[{
            'ID':'cut','PointList':[{'ID':'A','Position':{'x':0,'y':0}}, {'ID':'B','Position':{'x':24,'y':37}}]}]}]
        for shape in [n['PatternList'][0]['ShapeInfo']]+n['PatternList'][0]['InternalLineList']:
            for line in shape['LineList']:
                for point in line['PointList']: point['PointType']='Straight'
        r=g.conditional_line_candidate(n,'owner','shape','cut',{'owner':[3,0,0,3,20,30]},[240,220])
        self.assertTrue(r['candidate_available'],r)
        self.assertFalse(r['dispatch_authorized']); self.assertFalse(r['semantic_line_binding_verified'])
        missing=g.conditional_line_candidate(n,'owner','shape','cut',{},[240,220])
        self.assertFalse(missing['candidate_available']); self.assertEqual(missing['reason'],'INCOMPLETE_OWNER_PROJECTION_COVERAGE')
        n['PatternList'][0]['InternalLineList'].append(copy.deepcopy(n['PatternList'][0]['InternalLineList'][0]))
        n['PatternList'][0]['InternalLineList'][-1]['ID']='nearby'
        crowded=g.conditional_line_candidate(n,'owner','shape','cut',{'owner':[3,0,0,3,20,30]},[240,220])
        self.assertFalse(crowded['candidate_available']); self.assertEqual(crowded['reason'],'PROJECTED_OTHER_LINE_NOT_SEPARATED')


if __name__=='__main__': unittest.main()
