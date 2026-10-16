# Video Metadata Agent

A local Streamlit app that reads ZIP video-analysis exports, uses Azure OpenAI to
classify their JSON evidence and all readable thumbnails, and prepares metadata matching
the supplied CSV template. Source ZIP files are never modified or extracted to disk.

## Setup

Use Python 3.11 or newer. In PowerShell from this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` with your Azure resource endpoint, API key, deployment name, and API
version. The deployment must support image inputs and structured outputs (for
example, a compatible GPT-4o deployment). Use the deployment name configured in
Azure, which may differ from the model name. The default API version is
`2024-10-21`; change it if your deployment requires another supported version.
In Streamlit, `.env` values take precedence and reload on each interaction. Credentials are never included
in exports. No Azure resources are provisioned by this app.

## Run

### Saved model configuration

The following non-secret settings were read from the local `.env` on 2026-10-17:

| Setting | Configured value |
| --- | --- |
| Provider/client | Azure OpenAI / `AzureOpenAI` |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-4` |
| `AZURE_OPENAI_API_VERSION` | `2024-10-21` |
| API key | Stored only in `.env`; intentionally omitted here |
| Extraction API | `client.chat.completions.parse` |
| Response format | Pydantic schema generated from the CSV template |
| Request timeout / SDK retries | 90 seconds / 2 retries |
| Image detail / batch size | `high` / 6 images |
| Prepared image size / JPEG quality | Up to 2048 pixels per side / 95 |
| Text segment size / maximum segments | 24,000 characters / 64 |
| Product excerpt batch budget | 12,000 JSON characters |
| Opening Credits window | First 5 seconds |
| Temperature, top-p, seed, extraction output-token limit | Not explicitly set; service defaults apply |
| System prompt | `prompts/metadata_extraction.md`, reloaded per request |

`gpt-4` is the configured Azure **deployment name**, not verification of the
underlying model or version. Confirm that deployment's actual model supports
both images and Structured Outputs in Azure before processing. No model migration
or connection fix is implied by this documentation. The last local extraction
attempt failed to connect, so deployment capabilities remain unverified.
The app uses [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)
for schema-constrained responses; schema validity does not establish factual accuracy.
Runtime settings still come from `.env`; editing this snapshot does not configure the app.

### Start the app

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Open the displayed local URL. Choose **Upload ZIP files** and use **Choose ZIP
files** to select one or more ZIPs, then click **Process selected ZIPs**. Uploaded
ZIPs and results stay in memory until you choose a download. Each upload is limited
to 256 MiB. Alternatively, choose **Scan local folder** and set the input folder, click **Scan
folder**, select archives, and click **Process selected ZIPs**. Scanning is recursive
and excludes the legacy output folder and temporary test folders unless you explicitly
select a folder inside output. Each click creates a new run; ordinary
Streamlit reruns and downloads do not repeat processing. The app is intended for
local single-user use and reads folders on the machine running Streamlit.

## Results and evidence

Results are kept in the current Streamlit session, with these optional downloads.
No result files or run directories are created automatically. Download results
before closing the session or stopping the server; a new run replaces the displayed
results. Existing output files from older versions are left unchanged.

- `metadata.csv`: one row per successful outer ZIP, containing only the 16
  template columns. Multiple selections use semicolons; unknown values are blank.
- `metadata.json`: an array of metadata objects containing only template fields,
  without source references, paths, or explanations. Multiple selections are arrays; unknown scalars
  are null. All JSON exports use UTF-8; CSV uses UTF-8 with a BOM for Excel.
- `batch_status.json`: success/failure for every processed archive. Failures do
  not stop other archives. The status report is also prepared in memory.

Evidence supports the supplied transcript, OCR, computer vision labels, detected
objects, and moderation JSON formats. Other JSON is included as text. Oversized
text is processed in segments before final reconciliation. Every readable image,
including images in nested ZIPs, is reviewed at high detail in batches of six.
Final reconciliation combines observations from all batches, with up to six
images reattached for cross-checking, prioritizing explicitly timed opening images.
Images retain up to 2048 pixels per side and
JPEG quality 95. This requires more Azure requests than the previous eight-image
sampling. Archive order does not necessarily represent video timestamps; UUID
filenames cannot establish opening-frame timing. Emotion predictions
are excluded from metadata inference. Raw video and audio are not transcribed.

`Length` uses explicit source-video duration, falling back to transcript duration,
truncated to whole seconds. `Quality` uses explicit source-video resolution,
falling back to the source archive name, not thumbnail dimensions. Dates
require explicit video evidence; ZIP timestamps are ignored. Clearly associated
title-card or filename names can populate Credits, but do not establish ownership
or vendor. Content moderation does not establish usage rights. Unknown fields
require human follow-up even though export is automatic.
AI classifications may be incorrect; evidence is used internally for validation.

Timestamped OCR from the first five seconds is explicitly supplied to image review
and final reconciliation. Credits contains readable contributor names only; video
titles, grade labels, incidental background text and illegible OCR
are excluded. If opening frames have no usable credit text, Credits falls back
to a clearly identifiable contributor name in the video filename. Technical
suffixes and scene descriptions are omitted; if no name is identifiable, Credits
stays null. Readable opening title credits take priority over filename credits.
Explicit JSON image filenames linked to seconds, milliseconds, timecodes, or
frame indices with FPS identify opening images. Ambiguous filenames or conflicting
timestamps produce warnings; ZIP order and UUID names never establish timing.
Opening text and image source references are used internally;
the actual video filename is explicitly included in every text chunk, image batch,
and final request. Clear filename grade, school, product, footage-type, resolution,
and contributor tokens can populate their corresponding template fields. Actual
video evidence takes priority over conflicting filename hints. Generic date tokens
and brand/person mentions do not establish dates, ownership, or vendor roles.
the app separately shows image-review counts and warnings. If an image batch fails, the archive fails
rather than exporting metadata that pretends all images were reviewed. Corrupt
images are skipped with warnings and reflected in readable-image counts.

Text and all readable images are sent to your configured Azure deployment and may
incur API charges. No Box connection or continuous folder watcher is included.
Processing is sequential with SDK retries for transient errors (two retries,
bounded exponential backoff) and a 90-second timeout per request. Authorization,
deployment, vision, or structured-output errors appear as failed archives; ensure
your deployment supports both features. Up to 64 text segments are allowed.

Archive limits: 256 MiB compressed, 32 MiB per supported member, 256 MiB total
expanded supported members (including nested ZIP bytes), 10,000 entries, and two
nested ZIP levels. Unsupported/encrypted members are skipped with warnings.
CSV cells beginning with spreadsheet formula characters are prefixed with an
apostrophe; JSON retains original text.

## Customize and test

Role identifies speakers/interviewees using Teacher, Admin, Coach, CA Employee,
Student, and Parent/Family. People describes prominent visible people separately;
an off-camera speaker may have Role without being added to People. Unknown Role
is []. Neither role nor grade is guessed from voice characteristics or appearance.
Grade indexes affirmative grade mentions from transcripts and filenames, including
current and past teaching/coaching responsibilities and student grades. The former
teacher/coach interview exclusion has been removed. Mentioned ranges expand and
deduplicate; kindergarten normalizes to K, preschool to Pre-K. Grades 1–12 and
existing named categories are supported. Negations and unrelated numbers are not
grades. For example, "I teach fifth grade, not sixth" supports only ["5"].
Cached results from the older schema require fresh extraction to include Role.

Version 0.7 inventories every archive member and shows separate coverage counts
for prepared/reviewed JSON and images, opened nested ZIPs, and skipped or unreadable
files. Unsupported formats are reported, not executed or silently treated as read.
JSON preparation retains OCR frame context, transcript timing, object/label
observations, and explicit attributes with their original JSON paths. Emotion
files contribute spoken snippets only, never inferred personal emotions.
Explicit source-video duration/resolution wins over transcript duration or filename
resolution; equally explicit conflicts remain unknown with a warning. Image/page
dimensions are not treated as video properties. Long-input final reconciliation
receives selected original supporting excerpts as well as candidate summaries.
Every template field is reconsidered against all relevant evidence. Clear combined
clues can support content classifications and roles; identities, ownership, dates,
vendors, and locations remain evidence-based. Coverage stays separate from clean
metadata downloads. None of these operations autosaves results.

Version 0.4 adopts the reviewed example conventions. Subject is removed, Teaching
Sample is available, and Product contains Primary/Secondary objects. The known
parent of i-Ready Classroom Mathematics is Core Math; other products have a null
Primary until a mapping is supplied in `product_taxonomy.py`. CSV stores Product
as JSON in one cell to preserve this hierarchy; other arrays remain semicolon-separated.
Older result files are retained as originally generated.

Final reconciliation searches all prepared current-video JSON/OCR and archive-name
evidence. Every readable image is checked for product screens, covers, and logos.
Large sets of contextual product matches are reviewed in bounded batches before
final reconciliation; none are silently truncated. Product-review coverage and
skipped/unreadable member warnings stay separate from clean metadata exports.
Final reconciliation explicitly checks transcript product mentions with their
original context, including mentions omitted by chunk summaries. Negated or
unrelated mentions do not establish a product. Generic i-Ready does not establish
i-Ready Classroom Mathematics; only the latter maps to Core Math. Other products
retain a null Primary until a verified mapping is supplied.

Tagging emphasizes useful depicted activity and prominent People roles rather than
incidental backgrounds or activities merely discussed. Credits may use a clearly
associated title-card or filename name. Vendor and ownership still need explicit
evidence. Restrictions defaults to No under the requested catalog policy, preserves
explicit internal restrictions, and uses Yes—Internal Only for footage tagged
Assessment or Assessment Items—Restrictions Apply. Policy-derived restrictions
are validated internally against `metadata_policy`; this is a catalog default,
not a verified legal-rights determination.

Put desired-output JSON files from different videos in `examples/`. See
[examples/README.md](examples/README.md) for formats and copy the provided
`desired_output.json.example` starter to a `.json` filename. Add an optional video
description to improve relevance matching. Partial metadata examples are accepted;
invalid files are skipped with warnings shown in Streamlit.

Examples are loaded once per processing run. Up to five examples, within a combined
12,000-character budget, guide both chunk extraction and final reconciliation.
Their text is sent to Azure with each request; this can increase API usage. They
guide formatting and classification without training the model or supplying facts
about other videos. Selected example filenames stay internal and are not added
to metadata downloads. An empty folder works normally.
The generalized prompt supports interviews, classrooms, B-roll, products,
screencasts, green-screen footage, and audio-only assets within the existing template.

Edit `prompts/metadata_extraction.md` to change AI instructions. It is read on each
request. The original CSV defines fields, order, and enumerated choices; whitespace
and repeated Quality rows are normalized. UTF-8 and Windows-1252 CSV encodings
are supported. Add release notes to `CHANGELOG.md`.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests use mocked Azure responses and the supplied archive, including a Streamlit
scan/process/rerun smoke test. To check live integration, configure `.env` and
process the supplied archive through the UI. Its transcript duration should export
as `00:15:17` and source quality as `1080`; evidence supports Interview, Grade 3,
and i-Ready, but those classifications are model-generated.
