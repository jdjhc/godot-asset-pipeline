import json
import tempfile
import unittest
from pathlib import Path
from asset_pipeline.store import Store
from asset_pipeline.video import commit, sample

class VideoPickerTests(unittest.TestCase):
    def test_selection_validation_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory)
            folder=Path(directory)/'artifacts/asset_pipeline_video/video_test'
            folder.mkdir(parents=True)
            (folder/'source.mp4').write_bytes(b'video')
            frames=[]
            for i in (0,5,10):
                p=folder/f'{i}.png'; p.write_bytes(b'png')
                frames.append({'index':i,'seconds':i/24,'path':str(p.relative_to(directory))})
            (folder/'manifest.json').write_text(json.dumps({'source_path':str((folder/'source.mp4').relative_to(directory)),'source_sha256':'hash','extractor':'test','frames':frames}))
            for selection in ({'back':0,'left':5},{'front':0,'left':0},{'front':0,'left':6}):
                with self.assertRaises(ValueError): commit(store,{'session_id':'video_test','selection':selection})
            self.assertEqual(len(store.snapshot()['nodes']),0)
            params={'session_id':'video_test','selection':{'front':0,'right':10}}
            result=commit(store,params)
            self.assertEqual(commit(store,params),result)
            plan=store.plan(result['model_node_id'])
            self.assertEqual([e['role'] for e in plan['inputs']],['front','right'])
            self.assertEqual(plan['missing_inputs'],[])
            self.assertEqual(plan['prompt'],'')
            self.assertEqual(plan['params'],{'model':'P2-20260801','quad':False,'face_limit':16000,'texture':True,'pbr':True})
            node=store.get_node(result['inputs'][1]['node_id'])
            self.assertEqual(node['versions'][0]['metadata']['frame_index_zero_based'],10)

    def test_count_bounds(self):
        for count in (0,3,101,True,20.5):
            with self.assertRaises(ValueError): sample(None,{'count':count})
