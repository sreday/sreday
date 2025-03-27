#! /usr/bin/env bash

# print all the the options
lpoptions -p Brother1 -l

# print all the badges
find -s ./badges -name "*.png" \
    -exec echo "Printing" {} \; \
    -exec lpr -P Brother1  -o orientation-requested=5 -o PageSize=62x100mm -o Quality=High {} \;