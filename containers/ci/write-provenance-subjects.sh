#!/usr/bin/env bash
# Write deduplicated provenance subject JSON from a tab-separated tmp file.
#
# Usage (source from supply-chain.sh or tests):
#   source "$(dirname "${BASH_SOURCE[0]}")/write-provenance-subjects.sh"
#   write_provenance_subjects /tmp/subjects.json
write_provenance_subjects() {
  local list_subjects="$1"
  local line name digest invalid_rows=0 input_rows=0 unique_valid_rows=0 output_rows=0
  local -a fields=()

  if [[ -f "${list_subjects}.tmp" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" ]] && continue
      input_rows=$((input_rows + 1))
      fields=()
      IFS=$'\t' read -r -a fields <<<"$line"
      name="${fields[0]:-}"
      digest="${fields[1]:-}"
      if [[ ${#fields[@]} -ne 2 || -z "$name" || -z "$digest" ]]; then
        echo "ERROR: malformed provenance subject row (expected name<TAB>digest): ${line}" >&2
        invalid_rows=$((invalid_rows + 1))
      fi
    done < <(tr -d $'\r' <"${list_subjects}.tmp")

    if [[ "$invalid_rows" -ne 0 ]]; then
      return 1
    fi

    unique_valid_rows="$(
      tr -d $'\r' <"${list_subjects}.tmp" \
        | awk -F'\t' 'NF == 2 && $1 != "" && $2 != "" { print }' \
        | sort -u | wc -l | tr -d '[:space:]'
    )"

    tr -d $'\r' <"${list_subjects}.tmp" | sort -u \
      | jq -R -s 'split("\n")
          | map(select(length > 0) | split("\t"))
          | map(select(length == 2 and (.[0] | length > 0) and (.[1] | length > 0)))
          | map({name: .[0], digest: .[1]})' >"${list_subjects}"

    output_rows="$(jq 'length' "${list_subjects}")"
    if [[ "${output_rows}" -eq 0 ]]; then
      echo "ERROR: no valid provenance subjects in ${list_subjects}.tmp" >&2
      return 1
    fi
    if [[ "${output_rows}" -ne "${unique_valid_rows}" ]]; then
      echo "ERROR: provenance subject count mismatch (parsed ${output_rows}, expected ${unique_valid_rows})" >&2
      return 1
    fi
    echo "==> Provenance subjects: ${output_rows} of ${input_rows} rows (deduplicated)" >&2
    rm -f "${list_subjects}.tmp"
    echo "==> Wrote provenance subjects: ${list_subjects}" >&2
  else
    echo '[]' >"${list_subjects}"
  fi
}
