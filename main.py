import os
import struct
import tempfile
import argparse
import zipfile

from decompress import zflag_decompress, special_decompress
from decrypt import file_decrypt, XORDecryptor
from utils import get_decompression_algorithm_name, get_decryption_algorithm_name, parse_compression_type, parse_extension

#determines the info size by basic math (from the start of the index pointer // EOF or until NXFN data 
def determine_info_size(f, var1, hashmode, encryptmode, index_offset, files):
    if encryptmode == 256 or hashmode == 2:
        return 0x1C
    indexbuf = f.tell()
    f.seek(index_offset)
    buf = f.read()
    f.seek(indexbuf)
    return len(buf) // files

#reads an entry of the NPK index, if its 28 the file sign is 32 bits and if its 32 its 64 bits (NeoX 1.2 / 2 shienanigans)
def read_index(f, info_size, x, nxfn_files, index_offset):
    if info_size == 28:
        file_sign = [readuint32(f), f.tell() + index_offset]
    elif info_size == 32:
        file_sign = [readuint64(f), f.tell() + index_offset]
    file_offset = readuint32(f)
    file_length = readuint32(f)
    file_original_length = readuint32(f)
    zcrc = readuint32(f)                #compressed crc
    crc = readuint32(f)                 #decompressed crc
    zip_flag = readuint16(f)
    file_flag = readuint16(f)
    file_structure = nxfn_files[x] if nxfn_files else None
    return (
        file_sign,
        file_offset, 
        file_length,
        file_original_length,
        zcrc,
        crc,
        file_structure,
        zip_flag,
        file_flag,
        )

#data readers
def readuint64(f):
    return struct.unpack('Q', f.read(8))[0]
def readuint32(f):
    return struct.unpack('I', f.read(4))[0]
def readuint16(f):
    return struct.unpack('H', f.read(2))[0]
def readuint8(f):
    return struct.unpack('B', f.read(1))[0]

#formatted way to print data
def print_data(verblevel, minimumlevel, text, data, typeofdata, pointer=0):
    pointer = hex(pointer)
    match verblevel:
        case 1:
            if verblevel >= minimumlevel:
                print("{} {}".format(text, data))
        case 2:
            if verblevel >= minimumlevel:
                print("{} {}".format(text, data))
        case 3:
            if verblevel >= minimumlevel:
                print("{:10} {} {}".format(pointer, text, data))
        case 4:
            if verblevel >= minimumlevel:
                print("{:10} {} {}".format(pointer, text, data))
        case 5:
            if verblevel >= minimumlevel:
                print("{:10} {} {}   DATA TYPE:{}".format(pointer, text, data, typeofdata))

#main code
def unpack(args, statusBar=None):
    allfiles = []
    if args.selected_file:
        args.selected_file = args.selected_file - 1
    if args.verbose == None:
        args.verbose = 0
    try:
        #determines the files which the reader will have to operate on
        if args.input == None:
            allfiles = ["./" + x for x in os.listdir(args.input) if x.endswith(".npk")]
        elif os.path.isdir(args.input):
            allfiles = [args.input + "/" + x for x in os.listdir(args.input) if x.endswith(".npk")]
        else:
            allfiles.append(args.input)
    except TypeError as e:
        print("NPK files not found")
    if not allfiles:
        print("No NPK files found in that folder")
        
    #sets the decryption keys for the custom XOR cypher
    xor_decryptor = XORDecryptor(args.xor_key_file)

    #goes through every file
    for output in allfiles:
        
        #sets the final destination output
        print("UNPACKING: {}".format(output))
        folder_output = output[:-4]
        
        #makes the folder where the files will be dumped
        if not os.path.exists(folder_output):
            os.mkdir(folder_output)
            
        #opens the file
        with open(output, 'rb') as f:
            
            #this is the only thing that the force command does, doesnt read the bytes corresponding the NXPK / EXPK header
            if not args.force:
                data = f.read(4)
                pkg_type = None
                if data == b'NXPK':
                    pkg_type = 0
                elif data == b'EXPK':
                    pkg_type = 1
                else:
                    raise Exception('NOT NXPK/EXPK FILE')
                print_data(args.verbose, 1,"FILE TYPE:", data, "NXPK", f.tell())
            
            #amount of files
            files = readuint32(f)
            print_data(args.verbose, 1,"FILES:", files, "NXPK", f.tell())
            print("")
            
            #var1, its always set to 0
            var1 = readuint32(f)
            print_data(args.verbose, 5,"UNKNOWN:", var1, "NXPK_DATA", f.tell())
            
            #determines what i call "encryption mode", its 256 when theres NXFN file data at the end
            encryption_mode = readuint32(f)
            print_data(args.verbose, 2,"ENCRYPTMODE:", encryption_mode, "NXPK_DATA", f.tell())
            
            #determines what i call "hash mode", it can be 0, 1, 2, and 3, 0 and 1 are fine, 3 is not supported (i think) and 2 is unknown
            hash_mode = readuint32(f)
            print_data(args.verbose, 2,"HASHMODE:", hash_mode, "NXPK_DATA", f.tell())
            
            #offset where the index starts
            index_offset = readuint32(f)
            print_data(args.verbose, 2,"INDEXOFFSET:", index_offset, "NXPK_DATA", f.tell())

            #determines the "verbose_size" aka the size of each file offset data, it can be 28 or 32 bytes
            info_size = determine_info_size(f, var1, hash_mode, encryption_mode, index_offset, files)
            print_data(args.verbose, 3, "INDEXSIZE", info_size, "NXPK_DATA", 0)
            print("")

            index_table = []
            nxfn_files = []
            
            #checks for the "hash mode"
            if hash_mode == 2:
                print("HASHING MODE 2 DETECTED, MAY OR MAY NOT WORK!!")
                print("REPORT ERRORS ON GITHUB OR DISCORD <3")
            elif hash_mode == 3:
                raise Exception("HASHING MODE 3 IS CURRENTLY NOT SUPPORTED")
                
            #checks for the encryption mode and does the NXFN shienanigans
            if encryption_mode == 256 and args.nxfn_file:
                with open(folder_output+"/NXFN_result.txt", "w") as nxfn:
                    #data reader goes to where the NXFN file starts, it starts with b"NXFN" + 12 bytes (unknown for now)
                    f.seek(index_offset + (files * info_size) + 16)
                    
                    #nxfn file entries are plaintext bytes, separated by an empty byte
                    nxfn_files = [x for x in (f.read()).split(b'\x00') if x != b'']
                    
                    #dumps this file into a file called NXFN_result.txt
                    for nxfnline in nxfn_files:
                        nxfn.write(nxfnline.decode() + "\n")
            
            #does the same thing above, but doesnt write the file
            elif encryption_mode == 256:
                f.seek(index_offset + (files * info_size) + 16)
                nxfn_files = [x for x in (f.read()).split(b'\x00') if x != b'']

            #goes back to the index offset (or remains in the same place)
            f.seek(index_offset)

            #opens a temporary file
            with tempfile.TemporaryFile() as tmp:
                
                #reads the whole of the index file
                data = f.read(files * info_size)

                #if its an EXPK file, it decodes it with the custom XOR key
                if pkg_type:
                    data = xor_decryptor.decrypt(data)
                    
                #writes the data
                tmp.write(data)
                
                #goes to the start of the file
                tmp.seek(0)
                
                #checks if its only supposed to read one file, then it reads the data and adds it to a list as touples with the verbose itself
                if args.test:
                    index_table.append(read_index(tmp, info_size, 0, nxfn_files, index_offset))
                else:
                    for x in range(files):
                        index_table.append(read_index(tmp, info_size, x, nxfn_files, index_offset))
                        
            #calculates how many files it should analyse before reporting progress in the console (and adds 1 to not divide by 0)
            step = len(index_table) // 50 + 1

            #goes through every index in the index table
            for i, item in enumerate(index_table):
                if args.selected_file and (i != args.selected_file):
                    continue
                ext = None
                data2 = None
                
                #checks if it should print the progression text
                if ((i % step == 0 or i + 1 == files) and args.verbose <= 2 and args.verbose != 0) or args.verbose > 2:
                    print('FILE: {}/{}  ({}%)\n'.format(i + 1, files, ((i + 1) / files) * 100))
                    
                #unpacks the index
                file_sign, file_offset, file_length, file_original_length, zcrc, crc, file_structure, zflag, file_flag = item
                
                #prints the index data
                print_data(args.verbose, 4,"FILESIGN:", hex(file_sign[0]), "INFO_FILE", file_sign[1])
                print_data(args.verbose, 3,"FILEOFFSET:", file_offset, "FILE", file_sign[1] + 4)
                print_data(args.verbose, 3,"FILELENGTH:", file_length, "FILE", file_sign[1] + 8)
                print_data(args.verbose, 4,"FILEORIGLENGTH:", file_original_length, "INFO_FILE", file_sign[1] + 12)
                print_data(args.verbose, 4,"ZIPCRCFLAG:", zcrc, "INFO_FILE", file_sign[1] + 16)
                print_data(args.verbose, 4,"CRCFLAG:", crc, "INFO_FILE", file_sign[1] + 20)
                print_data(args.verbose, 3,"ZFLAG:", zflag, "INFO_FILE", file_sign[1] + 22)
                print_data(args.verbose, 3,"FILEFLAG:", file_flag, "INFO_FILE", file_sign[1] + 24)
                
                #goes to the offset where the file is indicated by the index
                f.seek(file_offset)
                
                #checks if its empty, and if include_empty is false, skips it
                if file_original_length == 0 and not args.include_empty:
                    continue
                
                #reads the amount of bytes corresponding to that file
                data = f.read(file_length)
                
                #defines the method for the file structure (if it has NXFN structure, if not its 00000000.extension)
                def check_file_structure():
                    if file_structure and not args.no_nxfn:
                        file_output = folder_output + "/" + file_structure.decode().replace("\\", "/")
                        os.makedirs(os.path.dirname(file_output), exist_ok=True)
                        ext = file_output.split(".")[-1]
                    else:
                        file_output = folder_output + '/{:08}.'.format(i)
                    return file_output

                #gets the file structure
                file_output = check_file_structure()

                #if its an EXPK file,it decrypts the data
                if pkg_type:
                    data = xor_decryptor.decrypt(data)
                    
                #prints out the decryption algorithm type    
                print_data(args.verbose, 5,"DECRYPTION:", get_decryption_algorithm_name(file_flag), "FILE", file_offset)

                #does the decryption
                data = file_decrypt(file_flag, data, args.key, crc, file_length, file_original_length)

                #prints out the compression type
                print_data(args.verbose, 5,"COMPRESSION:", get_decompression_algorithm_name(zflag), "FILE", file_offset)

                #does the decompression
                data = zflag_decompress(zflag, data, file_original_length)
                    
                #gets the compression type and prints it
                compression = parse_compression_type(data)
                print_data(args.verbose, 4,"COMPRESSION1:", compression.upper() if compression != None else "None", "FILE", file_offset)

                #does the special decompresison type (NXS and ROTOR)
                data = special_decompress(compression, data)

                #special code for zip files
                if compression == 'zip':
                    
                    #checks the file structure for zip files
                    file_output = check_file_structure() + "zip"
                    print_data(args.verbose, 5,"FILENAME_ZIP:", file_output, "FILE", file_offset)
                    
                    #writes the zip file data
                    with open(file_output, 'wb') as dat:
                        dat.write(data)
                        
                    #extracts the zip file
                    with zipfile.ZipFile(file_output, 'r') as zip:
                        zip.extractall(file_output[0:-4])
                        
                    #deletes the zip file 
                    if args.delete_compressed:
                        os.remove(file_output)
                        
                    #skips the rest of the code and goes on with the next index
                    continue

                #tries to guess the extension of the file

                if not file_structure:
                    ext = parse_extension(data)
                    file_output += ext
                
                print_data(args.verbose, 3,"FILENAME:", file_output, "FILE", file_offset)
                
                #writes the data
                with open(file_output, 'wb') as dat:
                    dat.write(data)
                    
                #converts KTX, PVR and ASTC to PNGs if the flag "convert_images" is set
                if (ext == "ktx" or ext == "pvr" or ext == "astc") and args.convert_images:
                    if os.name == "posix":
                        os.system('./lib/PVRTexToolCLI -i "{}" -d "{}png" -f r8g8b8a8 -noout'.format(file_output, file_output[:-len(ext)]))
                    elif os.name == "nt":
                        os.system('.\lib\PVRTexToolCLI.exe -i "{}" -d "{}png" -f r8g8b8a8 -noout'.format(file_output, file_output[:-len(ext)]))
        
        #prints the end time
        print("FINISHED - DECOMPRESSED FILES ".format(files))


# defines the parser arguments
def get_parser():
    parser = argparse.ArgumentParser()

    parser.add_argument('-i', '--input', help="Specify the input of the file or directory, if not specified will do all the files in the current directory", type=str)
    parser.add_argument('-o', '--output', help="Specify the output of the file or directory, if not specified will do all the files in the current directory", type=str)
    parser.add_argument('-x', '--xor-key-file', help="key file for xor decryption", default='neox_xor.key', type=str)
    parser.add_argument('-k', '--key', help="Select the key to use in the CRC128 hash algorithm (check the keys.txt for verbosermation)",type=int)

    parser.add_argument('-c', '--delete-compressed', action="store_true",help="Delete compressed files (such as ZStandard or ZIP files) after decompression")
    parser.add_argument('-m', '--merge-folder', help="Merge dumped files to output folder")
    parser.add_argument('--nxfn-file', action="store_true",help="Writes a text file with the NXFN dump output (if applicable)")

    parser.add_argument('-f', '--force', help="Forces the NPK file to be extracted by ignoring the header",action="store_true")
    parser.add_argument('--no-nxfn',action="store_true", help="Disables NXFN file structure")

    parser.add_argument('-v', '--verbose', help="Print verbosermation about the npk file(s) 1 to 5 for least to most verbose",type=int)
    parser.add_argument('-t', '--test', help='Export only one file from .npk file(s) for test', action='store_true')  
    parser.add_argument('-a', '--analyse', help='Analyse npk file(s) struct and save to file.')

    return parser.parse_args()

#entry point if ran as a standalone
if __name__ == '__main__':
    #defines the parser argument
    opt = get_parser()
    #runs the unpack script with the given arguments
    unpack(opt)