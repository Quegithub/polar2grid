ACSPO SST Reader
================

.. automodule:: polar2grid.readers.acspo

Command Line Arguments
----------------------

.. argparse::
    :module: polar2grid.readers.acspo
    :func: add_frontend_argument_groups
    :prog: polar2grid.sh acspo <writer>
    :passparser:

Examples:

.. code-block:: bash

    polar2grid.sh amsr2_l1b gtiff -h

    polar2grid.sh amsr2_l1b gtiff  -f <path to files>/<AMSR2 Level 1B filename>

    polar2grid.sh amsr2_l1b gtiff -g lcc_fit -f ../data/GW1AM2_201607201808_128A_L1DLBTBR_1110110.h5

    polar2grid.sh amsr2_l1b gtiff --rescale-configs $POLAR2GRID_HOME/rescale_configs/amsr2_png.ini -g lcc_fit -f ../data/GW1AM2_201607191903_137A_L1DLBTBR_1110110.h5

    polar2grid.sh amsr2_l1b awips --list-products -f /amsr2/GW1AM2_201607201808_128A_L1DLBTBR_1110110.h5

   polar2grid.sh amsr2_l1b awips -g 211e -p btemp_36.5h btemp_89.0av -f GW1AM2_201607191903_137A_L1DLBTBR_1110110.h5

