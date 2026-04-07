# Requires: uv (https://docs.astral.sh/uv/)
PNG := demo.png

.PHONY: all clean

all: $(PNG)

$(PNG): demo.sh
	command -v agg >/dev/null 2>&1 || uvx scriptcast install
	uvx scriptcast --output-dir . demo.sh

clean:
	rm -f $(PNG)
