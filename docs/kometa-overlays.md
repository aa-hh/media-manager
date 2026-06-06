# Kometa overlay setup — "Leaving Soon"

When you click **Delete in 2 Weeks** on the Free Space page, Media Manager does two things:

1. Records the item in a local SQLite queue with a deletion date 14 days out.
2. Adds a Plex label called **`Leaving Soon`** to the item in your Plex library.

After 14 days the item is deleted and the label is automatically removed.

The label itself does nothing visible in Plex — but if you run [Kometa](https://kometa.wiki) (formerly Plex Meta Manager), you can configure it to read that label and apply a banner overlay to the poster so users know the content is going away.

---

## What you need

- Kometa installed and running against your Plex server
- A `config/` directory for your Kometa configuration

---

## Step 1 — create the overlay file

Create a file called `leaving_soon.yml` somewhere Kometa can find it (e.g. alongside your other overlay files):

```yaml
overlays:
  Leaving_Soon:
    overlay:
      name: text(LEAVING SOON)
      font_size: 45
      font_color: "#FFFFFF"
      back_color: "#C0392B"
      back_width: 1000
      back_height: 70
      vertical_align: top
      horizontal_align: center
      vertical_offset: 0
    plex_search:
      label: Leaving Soon
```

Adjust font size, colours, and position to match your other overlays.

---

## Step 2 — reference it in your Kometa config

In your main `config.yml`, add the overlay file to each library you want it applied to:

```yaml
libraries:
  Movies:
    overlay_files:
      - file: config/leaving_soon.yml   # adjust path as needed

  TV Shows:
    overlay_files:
      - file: config/leaving_soon.yml
```

If you already have an `overlay_files` list, just append the entry.

---

## Step 3 — run Kometa

```bash
python kometa.py --run
```

Or let your scheduled Kometa run pick it up. Once it processes, any item with the `Leaving Soon` label will show the overlay on its poster in Plex.

---

## How the label lifecycle works

| Event | What happens to the Plex label |
|---|---|
| Click "Delete in 2 Weeks" | `Leaving Soon` label **added** |
| Click "Undo" before deletion | `Leaving Soon` label **removed** |
| Item is deleted automatically after 14 days | `Leaving Soon` label **removed** |
| Click "Delete Immediately" | Deleted immediately, no label applied |

If Kometa runs again after a label is removed, it will remove the overlay from the poster automatically.

---

## Troubleshooting

**Label is applied in Plex but no overlay appears** — run Kometa manually and check its output for errors. Make sure the `plex_search` label value matches exactly: `Leaving Soon` (two words, capital L and S).

**Overlay stays after item is deleted** — the item is gone from Plex, so this resolves itself on the next Kometa run when it can no longer find the item.

**I don't use Kometa** — no action needed. The `Leaving Soon` label is harmless if Kometa isn't configured to read it. The deletion queue and 14-day countdown work independently.
