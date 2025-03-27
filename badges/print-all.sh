#! /usr/bin/env bash
find ./badges -name "*.png" \
    -exec echo "Printing" {} \; \
    -exec lpr -P Brother1  -o orientation-requested=5 -o PageSize=62x100mm -o Quality=High {} \;