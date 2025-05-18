# from jarvis.db.figshare import data
# d = data('dft_3d') #choose a name of dataset from above
# # See available keys
# print (d[0].keys())
# # Dataset size
# print(len(d))
from jarvis.db.figshare import data
dft_3d = data(dataset="dft_3d_2021")
print (len(dft_3d))