from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from metadata_agent import ROOT, AzureExtractor, UploadedArchive, describe_error, load_template, run_batch, scan_archives
from reference_examples import EXAMPLES_DIR, load_examples
from metadata_agent import build_exports, format_metadata_value

load_dotenv(ROOT / '.env', override=True)
st.set_page_config(page_title='Video Metadata Agent', page_icon='🎬', layout='wide')
st.title('Video Metadata Agent')
st.caption('Turn ZIP analysis and video thumbnails into metadata matching your template.')
with st.sidebar:
    st.header('Folders')
    folder = st.text_input('Input folder', str(ROOT))
    st.caption('Processing sends analysis text and every readable nested-ZIP image to Azure OpenAI in batches.')
    st.caption('Results stay in this session. Download files only when you choose. Unknown fields remain blank.')
    if st.button('Check Azure connection'):
        try:
            AzureExtractor().check_connection()
            st.success('Azure deployment is reachable. You can process ZIP files.')
        except Exception as exc:
            st.error(describe_error(exc))

try:
    template = load_template()
except (OSError, ValueError) as exc:
    st.error(str(exc))
    st.stop()

examples, example_warnings = load_examples(template)
with st.sidebar:
    st.header('Reference examples')
    st.caption(f'{len(examples)} valid example(s) in {EXAMPLES_DIR}')
    st.caption('Add desired-output JSON files here. The next run uses up to five relevant examples. '
               'Selected example text is sent to Azure alongside the video evidence.')
    for warning in example_warnings:
        st.warning(warning)

source_mode = st.radio('Select ZIP files from', ['Upload ZIP files', 'Scan local folder'], horizontal=True)
uploaded_files = []
if source_mode == 'Upload ZIP files':
    uploaded_files = st.file_uploader('Choose ZIP files', type=['zip'], accept_multiple_files=True,
                                      max_upload_size=256,
                                      help='Select one or more video-analysis ZIP files. Nested thumbnail ZIPs are supported.')
    st.caption('The app opens the outer and nested ZIPs, reviews every readable image in batches, '
               'and checks opening OCR for Credits.')

if source_mode == 'Scan local folder' and st.button('Scan folder', type='primary'):
    try:
        legacy_output = ROOT / 'output'
        excluded = None if Path(folder).resolve().is_relative_to(legacy_output) else legacy_output
        st.session_state.archives = [str(p) for p in scan_archives(folder, excluded)]
        st.session_state.scan_folder = folder
        st.session_state.pop('selected_archives', None)
    except (OSError, ValueError) as exc:
        st.error(str(exc))

archives = st.session_state.get('archives', []) if source_mode == 'Scan local folder' else []
selected = []
if source_mode == 'Upload ZIP files' and uploaded_files:
    selected = uploaded_files
    st.write(f'{len(selected)} ZIP file(s) selected')
elif archives:
    st.write(f'{len(archives)} ZIP file(s) found in {st.session_state.scan_folder}')
    selected = st.multiselect('Archives to process', archives, default=archives, key='selected_archives')
if selected:
    if st.button('Process selected ZIPs', type='primary'):
        try:
            extractor = AzureExtractor()
            if source_mode == 'Upload ZIP files':
                selected = [UploadedArchive(file.name, file.getvalue()) for file in selected]
            bar = st.progress(0, text='Preparing archives')
            def update(done, total, name):
                bar.progress(done / max(1, total), text=f'{done}/{total} — {name}')
            with st.spinner('Extracting metadata with Azure OpenAI…'):
                exports, results, statuses = run_batch(selected, template, extractor, update)
            st.session_state.last_run = (exports, results, statuses)
        except Exception as exc:
            st.error(describe_error(exc))
elif source_mode == 'Scan local folder' and 'archives' in st.session_state:
    st.info('No ZIP files found. Choose another input folder and scan again.')

if 'last_run' in st.session_state:
    exports, results, statuses = st.session_state.last_run
    if any(set(result['metadata']) != {spec.name for spec in template} for result in results):
        st.warning('These results use an older metadata schema. Process the ZIP files again to include Role and mentioned grades.')
        st.stop()
    # Regenerate clean downloads for sessions started before this output change.
    exports = build_exports(template, results, statuses)
    successes = sum(s['status'] == 'success' for s in statuses)
    st.subheader('Results')
    st.write(f'{successes} succeeded · {len(statuses) - successes} failed')
    st.caption('Results are not saved automatically. Download them before closing this session.')
    if results:
        st.dataframe([{
            k: format_metadata_value(v) for k, v in r['metadata'].items()}
                      for r in results], hide_index=True, width='stretch')
    with st.expander('Unknown fields and warnings'):
        st.json([{'unknown_fields': r['unknown_fields'], 'warnings': r['warnings'],
                  'images_reviewed': r.get('image_review', {}).get('reviewed_images', 0),
                  'product_review': r.get('product_review', {}),
                  'archive_coverage': r.get('archive_coverage', {})} for r in results])
    for status in statuses:
        if status['status'] == 'failed':
            st.error(f"{Path(status['source_archive']).name}: {status['error']}")
    for filename in ['metadata.csv', 'metadata.json', 'batch_status.json']:
        st.download_button(f'Download {filename}', exports[filename], file_name=filename,
                           mime='text/csv' if filename.endswith('.csv') else 'application/json')
