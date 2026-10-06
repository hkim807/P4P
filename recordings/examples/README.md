# Example recordings

`sample.jsonl` contains three synthetic raw sensor frames, spaced 100 ms apart.
These are a CLI demonstration, not a live robot recording or calibration data.
Range values use the same native-unit representation as the sensor stream.

```bash
python3 -m app.replay recordings/examples/sample.jsonl
python3 -m app.replay recordings/examples/sample.jsonl --speed 0 --pretty
```
