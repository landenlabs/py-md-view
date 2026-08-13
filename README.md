<table border="0">
  <tr>
    <td>
      <!-- VERSION -->v1.00.00<br>
      <!-- DATE -->13-Aug-2026<br>
      macOS &nbsp;|&nbsp; Windows &nbsp;|&nbsp; Linux<br>
      <a href="https://landenlabs.com">Home</a>
    </td>
    <td>
      <a href="https://landenlabs.com">
        <img src="screens/landenlabs_400.webp" width="300" alt="LanDen Labs">
      </a>
    </td>
  </tr>
</table>

<img src="icon.png" width="72" align="left" alt="MD View icon">

# MD View

A small Qt Markdown viewer that also renders raw HTML embedded in the Markdown
source (tables, images, `<div>`/`<span>` blocks, etc.).

**By [LanDen Labs](https://github.com/landenlabs) (2026)**

---

## Usage

Open a file directly from the command line:

```
python3 md-view.py path/to/file.md
```

Or launch with no argument and use **File &rarr; Open...** (`Ctrl+O`) to pick a
file from the standard file browser. **File &rarr; Reload** (`Ctrl+R`)
re-renders the current file after editing it elsewhere.

## Features

- Renders standard Markdown (tables, fenced code blocks, lists, etc. via the
  `markdown` package's `extra`/`tables`/`fenced_code`/`sane_lists`/`toc`
  extensions).
- Passes raw HTML embedded in the Markdown source through to Qt's rich-text
  renderer, so inline `<img>`, `<table>`, `<div>` tags etc. display correctly.
- Relative image/link paths resolve against the loaded file's directory.
- Clicking a link to another local `.md` file loads it in place; other links
  open in the system browser.

## Requirements

```
pip install -r requirements.txt
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
