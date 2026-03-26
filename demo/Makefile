CAST      := demo.cast
GIF       := demo.gif
AGG       := .agg
FONTS_DIR := .fonts

# Detect platform for agg binary
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)

ifeq ($(UNAME_S),Linux)
  ifeq ($(UNAME_M),x86_64)
    AGG_ASSET := agg-x86_64-unknown-linux-gnu
  else ifeq ($(UNAME_M),aarch64)
    AGG_ASSET := agg-aarch64-unknown-linux-gnu
  else
    $(error Unsupported Linux architecture: $(UNAME_M))
  endif
else ifeq ($(UNAME_S),Darwin)
  ifeq ($(UNAME_M),arm64)
    AGG_ASSET := agg-aarch64-apple-darwin
  else
    AGG_ASSET := agg-x86_64-apple-darwin
  endif
else
  $(error Unsupported OS: $(UNAME_S))
endif

AGG_URL  := https://github.com/asciinema/agg/releases/latest/download/$(AGG_ASSET)
FONT_URL := https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip

.PHONY: all gif cast clean

all: gif

gif: $(GIF)

$(GIF): $(CAST) $(AGG) $(FONTS_DIR)
	./$(AGG) --font-dir $(FONTS_DIR) --renderer fontdue --font-family "JetBrains Mono" --rows 20 --cols 80 $(CAST) $(GIF)

$(CAST): demo.yaml generate.py
	uv run python generate.py

$(AGG):
	curl -fsSL -o $(AGG) $(AGG_URL)
	chmod +x $(AGG)

$(FONTS_DIR):
	mkdir -p $(FONTS_DIR)
	curl -fsSL -o $(FONTS_DIR)/JetBrainsMono.zip $(FONT_URL)
	unzip -q -o $(FONTS_DIR)/JetBrainsMono.zip "fonts/ttf/*.ttf" -d $(FONTS_DIR)
	mv $(FONTS_DIR)/fonts/ttf/*.ttf $(FONTS_DIR)/
	rm -rf $(FONTS_DIR)/fonts $(FONTS_DIR)/JetBrainsMono.zip

clean:
	rm -f $(GIF) $(CAST) $(AGG)
	rm -rf $(FONTS_DIR)
