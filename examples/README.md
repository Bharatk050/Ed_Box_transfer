# Desired metadata examples

Place one UTF-8 `.json` file per video directly in this folder. These are manually
corrected desired outputs, not ZIP analysis files. Use descriptive filenames.

Plain partial metadata is supported:

```json
{
  "Footage Type": "Interview",
  "People": ["Teacher"],
  "Content Owner": null
}
```

For better matching, add an optional description of the original example video:

```json
{
  "description": "Screen recording demonstrating a teacher dashboard workflow.",
  "metadata": {
    "Footage Type": "Screencast",
    "Content Tags": ["Product Demo", "Dashboard/UI"],
    "Grade": []
  }
}
```

Copy `desired_output.json.example` to a descriptive `.json` filename and edit it.
The starter file is deliberately not loaded. Include only fields whose corrected
values you know. Missing fields are allowed. Use exact template field names and
allowed choices; multiple selections must be arrays, unknown singles null, and
unknown multiple selections empty arrays. Dates use YYYY-MM-DD; Length hh:mm:ss;
State uses a full US state name. Invalid files are skipped with warnings.

Comments (`//` or `/* ... */`) and trailing commas are accepted in local example
files. Comments are ignored as data; catalog conventions live in the prompt and
validation code. Your original example files are not rewritten.

The current template has 16 fields: Subject was removed, Role was added, and Teaching Sample was
added as a footage type. Product output is hierarchical:

```json
{
  "Footage Type": "Teaching Sample",
  "Product": [{"Primary": "Core Math", "Secondary": "i-Ready Classroom Mathematics"}]
}
```

Legacy Product arrays of strings still load as examples and normalize to objects.
Only the known mapping above has a named Primary; other products use null, e.g.
`{"Primary": null, "Secondary": "i-Ready"}`. Additional parent mappings belong in
`product_taxonomy.py`. Unknown products remain an empty array. Human-only knowledge
in an example does not justify inferring that fact for another video.

Each file may contain one object or a one-record array, as downloaded by the app.
Multi-record arrays must be split into separate files. To reuse a
older report, copy one video's `metadata` object and optionally add a description.
For current clean downloads, copy one metadata object from the downloaded array
into its own example file.
Files are limited to 128 KiB, and each normalized example must fit within 12,000
characters. Selection uses keyword overlap against the current video evidence,
with filename ordering for ties or no overlap. Up to five examples fitting a
combined 12,000-character JSON budget are sent with each extraction request.

Examples reload when processing starts. Their text is sent to your Azure OpenAI
deployment and may increase usage costs. This is request-time guidance, not
permanent model training. Facts still require evidence from the current video.
