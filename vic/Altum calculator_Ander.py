# -*- coding: utf-8 -*-
"""
Created on Tue Apr 19 22:21:30 2022
@author: jenn_
"""

import glob
import re
import os
from osgeo.gdalconst import GA_ReadOnly
from osgeo import gdal, ogr
import numpy as np
from pandas import DataFrame, concat

gdal.PushErrorHandler('CPLQuietErrorHandler')

def getRasterBand(fn, band=1, access=0):
    ds = gdal.Open(fn)
    if ds is None:
        print("Error opening Dataset")
    band = ds.GetRasterBand(band).ReadAsArray()
    return band

def createRasterFromCopy(fn, ds, data, driverFmt="GTiff"):
    driver = gdal.GetDriverByName(driverFmt)
    outds = driver.CreateCopy(fn, ds, strict=0)
    outds.GetRasterBand(1).WriteArray(data)
    ds = None
    outds = None

def createRasterFromTemplate(fn, ds, data, ndv=-10000.0, driverFmt="GTiff"):
    driver = gdal.GetDriverByName(driverFmt)
    outds = driver.Create(fn, xsize=ds.RasterXSize, ysize=ds.RasterYSize, bands=1, eType=gdal.GDT_Float32)
    outds.SetGeoTransform(ds.GetGeoTransform())
    outds.SetProjection(ds.GetProjection())
    outds.GetRasterBand(1).SetNoDataValue(ndv)
    outds.GetRasterBand(1).WriteArray(data)
    outds = None
    ds = None

def SaveIndexRaster(folder, output, Index, band_ref, Index_name='_.tif'):
    dest_folder = os.path.join(output, os.path.split(folder)[1])
    os.makedirs(dest_folder, exist_ok=True)
    namefile = os.path.basename(folder) + Index_name
    outfn = os.path.join(dest_folder, namefile)
    createRasterFromTemplate(outfn, band_ref, Index)

# Initialize the band_names dictionary
band_names = {
    1: 'Blue',
    2: 'Green',
    3: 'Red',
    4: 'Red Edge',
    5: 'NIR',
    6: 'LWIR'
}

# ... (Previous code)

def bbox_to_pixel_offsets(gt, bbox):
    originX = gt[0]
    originY = gt[3]
    pixel_width = gt[1]
    pixel_height = gt[5]
    x1 = int((bbox[0] - originX) / pixel_width)
    x2 = int((bbox[1] - originX) / pixel_width) + 1

    y1 = int((bbox[3] - originY) / pixel_height)
    y2 = int((bbox[2] - originY) / pixel_height) + 1

    xsize = x2 - x1
    ysize = y2 - y1
    return (x1, y1, xsize, ysize)

def zonal_stats(vector_path, raster_path, rb, rgt, band_number, nodata_value=None, global_src_extent=False, shape_id='num'):
    results_dict = {}
    
    if len(re.split("[_.]", os.path.split(raster_path)[1])[0:3]) > 4:
        keys = '_'.join(re.split("[_.]", os.path.split(raster_path)[1])[0:3]) + '_' + ''.join(re.split("[_.]", os.path.split(raster_path)[1])[5]) + "_band_" + str(band_number)
        namefile = '_'.join(re.split("[_.]", os.path.split(raster_path)[1])[0:3]) + '.csv'
    else:
        keys = '_'.join(re.split("[_.]", os.path.split(raster_path)[1])[0:3]) + "_band_" + str(band_number)
        namefile = 'results.csv'
        
    results_dict[shape_id] = []
    results_dict[keys] = []
    results_dict['std_' + keys] = []
    results_dict['median_' + keys] = []
    results_dict['iqr_' + keys] = []
    results_dict['q90_' + keys] = []

    if nodata_value:
        nodata_value = float(nodata_value)
        rb.SetNoDataValue(nodata_value)

    vds = ogr.Open(vector_path, GA_ReadOnly)
    assert(vds)
    vlyr = vds.GetLayer(0)

    if global_src_extent:
        src_offset = bbox_to_pixel_offsets(rgt, vlyr.GetExtent())
        src_array = rb.ReadAsArray(*src_offset)

        new_gt = (
            (rgt[0] + (src_offset[0] * rgt[1])),
            rgt[1],
            0.0,
            (rgt[3] + (src_offset[1] * rgt[5])),
            0.0,
            rgt[5]
        )

    mem_drv = ogr.GetDriverByName('Memory')
    driver = gdal.GetDriverByName('MEM')

    stats = []
    feat = vlyr.GetNextFeature()

    while feat is not None:
        print('Processing shape id:', feat.GetField(shape_id))
        if not global_src_extent:
            src_offset = bbox_to_pixel_offsets(rgt, feat.geometry().GetEnvelope())
            src_array = rb.ReadAsArray(*src_offset)

            new_gt = (
                (rgt[0] + (src_offset[0] * rgt[1])),
                rgt[1],
                0.0,
                (rgt[3] + (src_offset[1] * rgt[5])),
                0.0,
                rgt[5]
            )

        mem_ds = mem_drv.CreateDataSource('out')
        mem_layer = mem_ds.CreateLayer('poly', None, ogr.wkbPolygon)
        mem_layer.CreateFeature(feat.Clone())

        rvds = driver.Create('', src_offset[2], src_offset[3], 1, gdal.GDT_Float32)
        rvds.SetGeoTransform(new_gt)
        gdal.RasterizeLayer(rvds, [1], mem_layer, burn_values=[1])
        rv_array = rvds.ReadAsArray()

        masked = np.ma.MaskedArray(
            src_array,
            mask=np.logical_or(
                src_array == nodata_value,
                np.logical_not(rv_array)
            )
        )

        feature_stats = {
            'mean': float(masked.mean()),
            'std': float(masked.std()),
            'median': float(np.median(masked.data)),  # Convert to regular NumPy array
            'iqr': float(np.percentile(masked.data, 75) - np.percentile(masked.data, 25)),  # Convert to regular NumPy array
            'q90': float(np.percentile(masked.data, 90)),  # Convert to regular NumPy array
            'fid': int(feat.GetFID())
        }

        results_dict[shape_id].append(feat.GetField(shape_id))
        mean = masked.mean()
        results_dict[keys].append(mean)
        std = masked.std()
        results_dict['std_' + keys].append(std)
        median = np.median(masked.data)
        results_dict['median_' + keys].append(median)
        iqr = np.percentile(masked.data, 75) - np.percentile(masked.data, 25)
        results_dict['iqr_' + keys].append(iqr)
        q90 = np.percentile(masked.data, 90)
        results_dict['q90_' + keys].append(q90)

        rvds = None
        mem_ds = None
        feat = vlyr.GetNextFeature()

    vds = None
    return results_dict, namefile

#Save
# Folder containing all the  *.tif files (different dates or flights)
folder =  'E:/PEM Lab/Ander_test/Green'

# Path where shape (*.shp) file is stored
input_zone_polygon = 'E:/PEM Lab/Ander_test/from QGIS/green_test.shp'

# Folder where results are stored
output_folder = 'E:/PEM Lab/Ander_test/results SPYDER'

# name of the file
namefile = 'results.csv'

os.makedirs(output_folder, exist_ok=True)
df1 = DataFrame()
df2 = DataFrame()

for f in glob.glob(os.path.join(folder, "*.tif")):
    print(f)
    rds = gdal.Open(f, GA_ReadOnly)
    for band_num in range(1, rds.RasterCount + 1):
        print("Processing Band:", band_num)
        input_raster = f
        assert(rds)
        rb = rds.GetRasterBand(band_num)
        rgt = rds.GetGeoTransform()
        results_dict, namefile = zonal_stats(input_zone_polygon, input_raster, rb, rgt, band_num, nodata_value=-10000.0)
        df1 = concat([df1, DataFrame(results_dict)], axis=1)

df2 = concat([df2, df1], axis=1)
df1 = DataFrame(None)
df3 = df2.loc[:, ~df2.columns.duplicated()].copy()
df3.to_csv(os.path.join(output_folder, namefile))

