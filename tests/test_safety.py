"""从业务入口验证缓存、规划、导出和离线复核，不调用真实云端。"""
import array
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch, Mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import semantic_planner as planner
import transcriber
import video_editor as editor
import video_assembler as assembler
from common import source_signature, write_json
from audio_extractor import probe_video_info

SENTENCE = {'text': '有效内容。', 'begin_time': 0, 'end_time': 1000,
            'words': [{'text': '有效内容', 'begin_time': 0, 'end_time': 1000}]}
SEGMENTS = transcriber._convert_bailian_output({'sentences': [SENTENCE]})


class PlanningSafetyTests(unittest.TestCase):
    def test_invalid_response_is_not_no_edits(self):
        for raw in ('broken', '{}', '{"edits": null}', '{"edits": [null]}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                planner.extract_json_from_text(raw)
        self.assertEqual(planner.extract_json_from_text('{"edits": []}'), {'edits': []})

    def test_semantic_planning_requires_agent_plan(self):
        segments = [{'start': i * 10., 'end': i * 10. + 5, 'text': '解释。'} for i in range(50)]
        with self.assertRaisesRegex(RuntimeError, 'Agent'):
            planner.plan_video_cuts(segments)
        self.assertEqual(planner.plan_video_cuts(segments, {'edits': []}), [])

    def test_circular_replacements_are_retained(self):
        segments = [{'start': 0., 'end': 1., 'text': '解释。'}, {'start': 1., 'end': 2., 'text': '解释。'}]
        edits = [{'remove_start_id': a, 'remove_end_id': a, 'replacement_ids': [b],
                  'removed_quote': '解释。', 'confidence': 'high', 'category': 'duplicate_take'}
                 for a, b in [('U0001', 'U0002'), ('U0002', 'U0001')]]
        self.assertEqual(planner.plan_video_cuts(segments, {'edits': edits}), [])
        cuts = planner.plan_video_cuts(segments, {'edits': edits[:1]})
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]['replacement_ids'], ['U0002'])

    def test_context_is_local_and_long_local_cleanup_is_rejected(self):
        segments = [{'start': 0., 'end': 3., 'text': '这是一段口播。'},
                    {'start': 3.5, 'end': 7., 'text': '后面的内容。'}]
        context = planner.build_semantic_context(segments)
        self.assertIn('windows', context)
        self.assertIn('FULL TRANSCRIPT', context['windows'][0]['prompt'])
        plan = {'edits': [{
            'remove_start_id': 'U0001', 'remove_end_id': 'U0001',
            'replacement_ids': [], 'removed_quote': '这是一段口播。',
            'confidence': 'high', 'category': 'delivery_cleanup',
        }]}
        self.assertEqual(planner.plan_video_cuts(segments, plan), [])

    def test_waveform_snap_never_expands_cut(self):
        with tempfile.TemporaryDirectory() as d:
            audio = Path(d) / 'audio.wav'
            pcm = array.array('h', [1000] * 48000)
            pcm[int(.945 * 16000)] = pcm[int(2.055 * 16000)] = 0
            with wave.open(str(audio), 'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
                f.writeframes(pcm.tobytes())
            cut = planner.refine_cut_boundaries_to_minima([{'start_ms': 1000, 'end_ms': 2000}], audio)[0]
            self.assertGreaterEqual(cut['start_ms'], 1000)
            self.assertLessEqual(cut['end_ms'], 2000)
            self.assertLess(cut['start_ms'], cut['end_ms'])

    def test_windows_uses_software_encoder_fallback(self):
        with patch.object(assembler.platform, 'system', return_value='Windows'):
            encoder, options = assembler.detect_video_encoder()
        self.assertEqual(encoder, 'libx264')
        self.assertIn('-crf', options)


class TranscriptSafetyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), '需要 Node.js')
    def test_credential_injection_reaches_real_python_reader(self):
        profile = Path(editor.__file__).parent / 'credential-ui' / 'src' / 'profile.ts'
        # Inject a test backend; never read or modify the user's system key store.
        js = '''
const {loadProfile, prepareProfile} = await import(process.argv[1]);
const {spawnSync} = await import('node:child_process');
const bindings = await loadProfile('default');
const env = {...process.env};
delete env.DASHSCOPE_API_KEY;
const plan = await prepareProfile(bindings, process.argv.slice(2), env, async () => 'test-only-credential');
const result = spawnSync(plan.command, plan.args, {env: plan.env, encoding: 'utf8'});
process.stdout.write(result.stdout);
process.stderr.write(result.stderr);
process.exit(result.status ?? 1);
'''
        py = ('import sys; sys.path.insert(0, sys.argv[1]); '
              'from transcriber import load_api_key; '
              'print(load_api_key() == "test-only-credential")')
        result = subprocess.run(['node', '--input-type=module', '-e', js, profile.as_uri(),
                                 sys.executable, '-c', py, str(Path(editor.__file__).parent)],
                                check=True, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        self.assertEqual(result.stdout.strip(), 'True')
        self.assertNotIn('test-only-credential', result.stdout + result.stderr)

    def test_sdk_entry_uses_sentence_accessor(self):
        sdk = ModuleType('dashscope')
        audio = ModuleType('dashscope.audio')
        asr = ModuleType('dashscope.audio.asr')
        asr.Recognition = Mock(return_value=SimpleNamespace(call=Mock(return_value=SimpleNamespace(
            status_code=200, get_sentence=lambda: [SENTENCE]))))
        with tempfile.TemporaryDirectory() as d, patch.dict(sys.modules, {
            'dashscope': sdk, 'dashscope.audio': audio, 'dashscope.audio.asr': asr,
        }), patch.object(transcriber.shutil, 'which', return_value=None), patch.object(transcriber, 'load_api_key', return_value='fake'):
            segments = transcriber.transcribe_audio_bailian(Path(d) / 'audio.wav', Path(d) / 'transcript.json')
        self.assertEqual(segments, SEGMENTS)
        self.assertEqual(asr.Recognition.call_args.kwargs['model'], 'paraformer-realtime-v2')

    def test_unknown_or_empty_asr_fails(self):
        with self.assertRaises(ValueError):
            transcriber._convert_bailian_output({'unknown': []})
        for segments in ([], [{'start': 0, 'end': 1, 'words': []}]):
            with self.assertRaises(ValueError):
                transcriber.validate_transcript(segments)

    def test_seconds_after_one_minute_stay_seconds(self):
        segments = transcriber._convert_bailian_output({'sentences': [
            {'text': '内容', 'start': 70, 'end': 71,
             'words': [{'text': '内容', 'start': 70, 'end': 71}]}]})
        self.assertEqual(segments[0]['words'][0]['start'], 70)


class PipelineSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / 'source.mp4'
        self.video.write_bytes(b'source one')
        semantic_dir, _, _ = editor.work_paths(self.video)
        semantic_dir.mkdir(parents=True, exist_ok=True)
        write_json(semantic_dir / 'semantic_plan.json', {
            'source': source_signature(self.video), 'edits': []
        })
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        patches = {
            'get_config': {'return_value': editor.DEFAULT_CONFIG.copy()},
            'load_api_key': {'return_value': 'fake'},
            'probe_video_info': {'return_value': {'duration_s': 8, 'has_video': True, 'has_audio': True}},
            'measure_audio_levels': {'return_value': (-20, -5)},
            'measure_energy_percentiles': {'return_value': (-40, -10)},
            'detect_silence_regions': {'return_value': []},
            'detect_nonspeech_regions_vad': {'return_value': []},
            'transcribe_audio_bailian': {'return_value': SEGMENTS},
            'plan_video_cuts': {'return_value': []},
            'refine_cut_boundaries_to_minima': {'side_effect': lambda cuts, audio: cuts},
            'extract_audio_wav': {'side_effect': lambda video, target: target.write_bytes(b'audio')},
        }
        self.mocks = {name: self.stack.enter_context(patch.object(editor, name, **opts))
                      for name, opts in patches.items()}

    def test_legacy_remote_semantic_config_is_ignored(self):
        config = Path(self.tmp.name) / 'config.json'
        write_json(config, {
            'model': 'old-remote-model',
            'api_base': 'https://old.example.invalid/v1',
            'enable_thinking': True,
            'concurrency': 9,
        })
        with patch.object(editor, 'CONFIG_PATH', config):
            loaded = editor.get_config()
        self.assertNotIn('model', loaded)
        self.assertNotIn('api_base', loaded)
        self.assertEqual(loaded['semantic_planner'], 'calling-agent')
        self.assertEqual(loaded['asr_backend'], 'bailian')

    def test_local_asr_config_is_rejected_in_video_editor(self):
        config = Path(self.tmp.name) / 'config.json'
        write_json(config, {'asr_backend': 'local'})
        with patch.object(editor, 'CONFIG_PATH', config), patch.object(
            editor, 'get_config', return_value={**editor.DEFAULT_CONFIG, 'asr_backend': 'local'}
        ):
            with self.assertRaisesRegex(ValueError, '固定为 bailian'):
                editor.run_pipeline(self.video, dry_run=True)

    def test_cache_reuses_same_source_but_invalidates_replacement(self):
        editor.run_pipeline(self.video, dry_run=True)
        editor.run_pipeline(self.video, dry_run=True)
        self.assertEqual(self.mocks['extract_audio_wav'].call_count, 1)
        self.video.write_bytes(b'source two')
        write_json(editor.work_paths(self.video)[0] / 'semantic_plan.json', {
            'source': source_signature(self.video), 'edits': []
        })
        editor.run_pipeline(self.video, dry_run=True)
        self.assertEqual(self.mocks['extract_audio_wav'].call_count, 2)
        self.assertNotEqual(editor.work_paths(self.video)[0], editor.work_paths(self.video.with_suffix('.mov'))[0])

    def test_missing_agent_plan_only_prepares_local_context(self):
        work, _, _ = editor.work_paths(self.video)
        (work / 'semantic_plan.json').unlink()
        result = editor.run_pipeline(self.video, dry_run=True)
        self.assertTrue(result['needs_agent_plan'])
        context = json.loads(Path(result['semantic_context']).read_text(encoding='utf-8'))
        self.assertEqual(context['source'], source_signature(self.video))
        self.assertIn('windows', context)

    def test_agent_plan_must_match_current_source(self):
        work, _, _ = editor.work_paths(self.video)
        write_json(work / 'semantic_plan.json', {'source': {'sha256': 'stale'}, 'edits': []})
        with self.assertRaisesRegex(ValueError, 'source'):
            editor.run_pipeline(self.video, dry_run=True)

    def test_review_is_offline_and_preserves_plan(self):
        result = editor.run_pipeline(self.video, dry_run=True)
        before = Path(result['plan_json']).read_bytes()
        self.mocks['load_api_key'].side_effect = AssertionError('离线操作不得读 Key')
        self.mocks['plan_video_cuts'].side_effect = AssertionError('离线操作不得调用模型')
        self.assertEqual(editor.review_plan(self.video)['kept_intervals'], [(0., 8000.)])
        self.assertEqual(Path(result['plan_json']).read_bytes(), before)
        self.video.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            editor.review_plan(self.video)

    def test_manual_plan_rejects_invalid_intervals(self):
        result = editor.run_pipeline(self.video, dry_run=True)
        plan = json.loads(Path(result['plan_json']).read_text(encoding='utf-8'))
        for kept in ([], [[0, float('nan')]], [[0, 9000]], [[2000, 4000], [3000, 5000]]):
            with self.subTest(kept=kept), self.assertRaises(ValueError):
                editor.validate_plan({**plan, 'kept_intervals': kept}, self.video)

    def test_failure_writes_no_success_plan(self):
        self.mocks['plan_video_cuts'].side_effect = RuntimeError('模拟失败')
        with self.assertRaises(RuntimeError):
            editor.run_pipeline(self.video, dry_run=True)
        self.assertFalse(editor.work_paths(self.video)[1].exists())
        self.assertFalse(editor.work_paths(self.video)[2].exists())

    def test_final_guard_rejects_semantic_cut_into_retained_word(self):
        semantic_cut = {
            'start_ms': 100, 'end_ms': 800,
            'spoken_start_ms': 200, 'spoken_end_ms': 300,
            'replacement_intervals': [],
        }
        with patch.object(editor, 'detect_adaptive_pauses', return_value=[]), \
             patch.object(editor, 'plan_video_cuts', return_value=[semantic_cut]):
            with self.assertRaisesRegex(ValueError, '保留文字'):
                editor.run_pipeline(self.video, dry_run=True)

    def test_confirmed_audio_pause_may_overlap_asr_word(self):
        with patch.object(editor, 'detect_adaptive_pauses', return_value=[
            {'start_ms': 100, 'end_ms': 800, 'source': 'silence'}
        ]):
            result = editor.run_pipeline(self.video, dry_run=True)
        self.assertEqual(result['semantic_cuts'], [])
        self.assertEqual(result['pause_cuts'][0]['source'], 'silence')

    def test_short_keep_not_merged_away(self):
        with patch.object(editor, 'detect_adaptive_pauses', return_value=[
            {'start_ms': 2000, 'end_ms': 3000}, {'start_ms': 3040, 'end_ms': 4000}
        ]):
            result = editor.run_pipeline(self.video, dry_run=True)
            self.assertIn((3000., 3040.), result['kept_intervals'])

    def test_dry_report_does_not_claim_export(self):
        result = editor.run_pipeline(self.video, dry_run=True)
        report = Path(result['report_md']).read_text(encoding='utf-8')
        self.assertIn('尚未成功导出', report)
        self.assertNotIn('音频微淡化已应用', report)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), '需要 ffmpeg/ffprobe')
class ExportSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.video = cls.root / 'source.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=30:duration=8', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=8', '-c:v', 'libx264',
            '-g', '60', '-keyint_min', '60', '-sc_threshold', '0', '-c:a', 'aac', str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_export_paths_protect_original_and_links(self):
        symlink = self.root / 'source-link.mp4'
        hardlink = self.root / 'source-hardlink.mp4'
        symlink.symlink_to(self.video)
        os.link(self.video, hardlink)
        before = self.video.read_bytes()
        for func in (assembler.assemble_video_encode, assembler.assemble_video_stream_copy):
            for target in (self.video, symlink, hardlink):
                with self.subTest(func=func.__name__, target=target), self.assertRaises(ValueError):
                    func(self.video, [(0, 2000)], target, overwrite=True)
        self.assertEqual(self.video.read_bytes(), before)

    def test_failed_render_preserves_existing_output(self):
        target = self.root / 'existing.mp4'
        target.write_bytes(b'previous export')
        with self.assertRaises(FileExistsError):
            assembler.assemble_video_encode(self.video, [(0, 2000)], target)
        with patch.object(assembler, 'probe_video_info', side_effect=[
            probe_video_info(self.video), {'has_video': False, 'has_audio': True, 'duration_s': 2}
        ]):
            with self.assertRaises(RuntimeError):
                assembler.assemble_video_encode(self.video, [(0, 2000)], target, overwrite=True)
        self.assertEqual(target.read_bytes(), b'previous export')

    def test_encode_and_copy_record_actual_duration(self):
        intervals = [(1500, 2500), (4500, 5500)]
        for copy in (False, True):
            out = self.root / f'export-{copy}.mp4'
            report = self.root / f'report-{copy}.md'
            plan = {'original_duration_s': 8, 'new_duration_s': 2, 'kept_intervals': intervals,
                    'config': {'crossfade_ms': 15}, 'pause_cuts': [], 'semantic_cuts': []}
            actual = editor.export_plan(plan, self.video, out, report, fast_copy=copy)
            self.assertAlmostEqual(actual, probe_video_info(out)['duration_s'])
            text = report.read_text(encoding='utf-8')
            self.assertIn(f'{actual:.3f}s', text)
            if copy:
                self.assertIn('近似流拷贝', text)
                self.assertNotIn('音频微淡化已应用', text)
            else:
                self.assertAlmostEqual(actual, 2, delta=.1)
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(out), '-f', 'null', '-'],
                           check=True, capture_output=True)

    def test_cli_review_and_render_without_credentials(self):
        work, plan_path, _ = editor.work_paths(self.video)
        write_json(plan_path, {'schema_version': 1, 'source': source_signature(self.video),
            'input_video': str(self.video), 'original_duration_s': 8, 'new_duration_s': 8,
            'kept_intervals': [[0, 1000], [2000, 3000]], 'config': {'crossfade_ms': 15},
            'pause_cuts': [], 'semantic_cuts': []})
        env = {k: v for k, v in os.environ.items() if k != 'DASHSCOPE_API_KEY'}
        env['HOME'] = str(self.root)
        entry = Path(editor.__file__)
        for command in ('review', 'render'):
            args = [sys.executable, str(entry), command, str(self.video)]
            if command == 'render':
                args += ['-o', str(self.root / 'offline.mp4')]
            completed = subprocess.run(args, env=env, capture_output=True, text=True,
                                       encoding='utf-8', errors='replace', check=True)
            result = json.loads(completed.stdout)
            self.assertEqual(result['new_duration_s'], 2)
            if command == 'render':
                self.assertAlmostEqual(result['actual_duration_s'], 2, delta=.1)


if __name__ == '__main__':
    unittest.main()
