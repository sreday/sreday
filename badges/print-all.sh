#! /usr/bin/env bash

PRINTER=Brother1
SRC=./badges
OPTIONS="-o orientation-requested=5 -o PageSize=62x100mm -o Quality=High"

# print all the the options
lpoptions -p $PRINTER -l

# print all the badges
find -s $SRC \
    -name "*.png" \
    -exec echo "Printing" {} \; \
    -exec lpr -P $PRINTER $OPTIONS {} \;
