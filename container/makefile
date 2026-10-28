
#	Get the download URL of all zip files in the latest release and download them to the build/exts directory
all: 
	@mkdir -p "build/exts"
	@DL_URLs=$$(curl -s https://api.github.com/repos/ManufacturingTechnology/DTP-Isaac-Sim-Service/releases/latest | jq -r '.assets[] | select(.name | test("\\.zip$$")) | .browser_download_url') \
		&& echo "Extension URLs:\n$${DL_URLs}" \
		&& echo "$${DL_URLs}" | while IFS= read -r DL_URL; do \
			DL_FILENAME=$$(basename "$${DL_URL}") \
			&& DL_OUTPUT="build/exts/$${DL_FILENAME}" \
			&& echo "\nExtension:\n  - URL: $${DL_URL}\n  - Filename: $${DL_FILENAME}\n  - Output: $${DL_OUTPUT}" \
			&& curl -s -L -o "$${DL_OUTPUT}" "$${DL_URL}"; \
		done

#	Build the extension from this repository
dev: build/exts/amt.dtp.ise-*.zip

build/exts/amt.dtp.ise-*.zip: ../extension/dist/amt.dtp.ise-*.zip
	mkdir -p build/exts/
	cp ../extension/dist/amt.dtp.ise-*.zip build/exts/
	
../extension/dist/amt.dtp.ise-*.zip:
	cd "../extension/" && make

config:
	docker cp amt-dtp-iss:"/isaac-sim/.local/share/ov/data/Kit/Isaac-Sim Streaming/5.1/user.config.json" "build/user.config.json"

clean:
	rm -rf build/exts/
	docker compose down || true
	docker image rm amt-dtp-iss || true
	docker volume rm amt-dtp-iss-cache-main || true
	docker volume rm amt-dtp-iss-cache-compute || true
	docker volume rm amt-dtp-iss-logs || true
	docker volume rm amt-dtp-iss-config || true
	docker volume rm amt-dtp-iss-data || true
	docker volume rm amt-dtp-iss-pkg || true
	docker volume rm amt-dtp-iss-files || true

