from bz2 import compress
from operator import mod
import PySimpleGUI as sg	# pip install pysimplegui
import PIL.Image			# pip install pillow
from enum import Enum		# requires python 3.10 or higher
import io
import threading

from numpy import imag

THREADWAIT = 0.1

# CGA / 16 Color palette generated with the following formula:
# red   = 2/3×(colorNumber & 4)/4 + 1/3×(colorNumber & 8)/8 * 255
# green = 2/3×(colorNumber & 2)/2 + 1/3×(colorNumber & 8)/8 * 255
# blue  = 2/3×(colorNumber & 1)/1 + 1/3×(colorNumber & 8)/8 * 255
WINDOWS16COLORPAL = [
	0, 0, 0,		# black
	0, 0, 170,		# blue
	0, 170, 0,		# green
	0, 170, 170,	# cyan
	170, 0, 0,		# red
	170, 0, 170,	# magenta
	170, 170, 0,	# dark yellow
	170, 170, 170,	# light gray
	85, 85, 85,		# dark gray
	85, 85, 255,	# light blue
	85, 255, 85,	# light green
	85, 255, 255,	# light cyan
	255, 85, 85,	# light red
	255, 85, 255,	# light magenta
	255, 255, 85,	# yellow
	255, 255, 255	# white
]
# 0b0010 1100 = 0x2C
# Image display: 00xx0x00
# Image display: 00x0xx00

class ConversionType(Enum):
	UNDEFINED	= 0
	INDEXED		= 1
	MONOCHROME 	= 2
	RGB16C		= 3
	RGB332 		= 4
	RGB565 		= 5
	RGB888 		= 6

class CompressionType(Enum):
	NO_COMPRESSION 	= 0
	RLE_COMPRESSION = 1

def convToBytes(image, resize=None):
	img = image.copy()
	
	cur_width, cur_height = img.size
	if resize:
		new_width, new_height = resize
		scale = min(new_height/cur_height, new_width/cur_width)
		img = img.resize((int(cur_width*scale), int(cur_height*scale)), PIL.Image.BILINEAR)
	
	ImgBytes = io.BytesIO()
	img.save(ImgBytes, format="PNG")
	del img
	return ImgBytes.getvalue()

def resizeImage(window, image, offsets):
	oldWindowSize = window.size
	# Resize the image based on the free space available for the [image] element by substracting the offsets from the window size
	# (Basically avoiding to check the [image]-element's size, trying to save calls here by working with stored variables)
	window['-IMGBOX-'].update(convToBytes(image, (oldWindowSize[0] - offsets[0], oldWindowSize[1] - offsets[1])))

def convertImage(image, resize, conversion, dithering):
	ImageToProcess = image.resize(tuple(resize),PIL.Image.LANCZOS)
	ImageToReturn = image.copy()
	match conversion:
		case ConversionType.MONOCHROME:
			ImageToReturn = ImageToProcess.convert("1", dither=dithering)
		case ConversionType.RGB16C:
			palImage = PIL.Image.new('P', (16,16))
			palList = WINDOWS16COLORPAL.copy()
			palList.extend(palList[:3] * 240)
			palImage.putpalette(palList)
			ImageToReturn = ImageToProcess.quantize(16, palette=palImage, dither=dithering)
		case ConversionType.RGB332:
			palImage = PIL.Image.new('P', (16,16))
			palList = []
			for colors in range(256):
				palList.extend([colors & 0b11100000, (colors & 0b00011100) << 3, (colors & 0b00000011) << 6])
			palImage.putpalette(palList)
			ImageToReturn = ImageToProcess.quantize(256, palette=palImage, dither=dithering)
		case ConversionType.RGB565:
			ImageToReturn = ImageToProcess.copy()
			for x in range(ImageToReturn.width):
				for y in range(ImageToReturn.height):
					color = list(ImageToReturn.getpixel((x,y)))
					color[0] = color[0] & 0xF8
					color[1] = color[1] & 0xFC
					color[2] = color[2] & 0xF8
					ImageToReturn.putpixel((x,y), tuple(color))
		case ConversionType.RGB888:
			ImageToReturn = ImageToProcess.copy()
	del ImageToProcess
	return ImageToReturn

def rle_compress(pixelArray):
	# Todo: Image compression
	return pixelArray

def convertToPIF(image, resize, conversion, dithering, compression):
	class CompressionMode(Enum):
		NOTHING	= 0
		PREPARE = 1
		REPEATIVE = 2
		INDIVIDUAL = 3
	class ImageH(Enum):
		IMAGETYPE = 0
		BITSPERPIXEL = 1
		IMAGEWIDTH = 2
		IMAGEHEIGHT = 3
		IMAGESIZE = 4
		COLTABLESIZE = 5
		COMPRTYPE = 6
	
	imageHeader = [0,0,0,0,0,0,0]
	colorTable = []
	# Image Data contains the pixels in word-size (min 1 byte, max 3 bytes)
	# Proper conversion done when saving the data, BITSPERPIXEL to process correctly
	imageData = []

	imageWord = 0
	imageCnt = 0
	color = 0

	TemporaryImage = image.copy()

	#Write the image header with the information we already have
	imageHeader[ImageH.IMAGEWIDTH] = TemporaryImage.width
	imageHeader[ImageH.IMAGEHEIGHT] = TemporaryImage.height
	if (compression):
		imageHeader[ImageH.COMPRTYPE] = 0x7DDE

	if ((conversion != ConversionType.RGB888) and (conversion != ConversionType.RGB565)):
		TemporaryImage = convertImage(image, resize, conversion, dithering)
	else:
		TemporaryImage = convertImage(image, resize, ConversionType.RGB888, dithering)

	match conversion:
		case ConversionType.MONOCHROME:
			# Write the Image Type and Bits per Pixel into the imageHeader
			imageHeader[ImageH.IMAGETYPE] = 0x7DAA	# B/W mode selected
			imageHeader[ImageH.BITSPERPIXEL] = 8		# 8 Bits per Pixel
			# Check every pixel and pack to a group of 8 within a byte
			for y in range(TemporaryImage.height):
				for x in range(TemporaryImage.width):
					color = TemporaryImage.getpixel((x,y))
					if (color): # 0 = black, 255 = white
						imageWord = imageWord | (1 << imageWord)
					imageCnt = imageCnt + 1
					if (imageCnt >= 8):
						imageData.append(imageWord)
						imageWord = 0
						imageCnt = 0
			# Add remaining Pixels, if there are any
			if (imageCnt > 0):
				imageData.append(imageWord)
		case ConversionType.RGB16C:
			# Check every pixel and pack to a group of 2 within a byte
			for y in range(TemporaryImage.height):
				for x in range(TemporaryImage.width):
					color = TemporaryImage.getpixel((x,y))
					# Check which of the 16 colors is used to return a value between 0 and 15
					for col in range(16):
						if (color == WINDOWS16COLORPAL[(col*3):3]):
							imageWord = imageWord | (col << ((imageCnt % 2) * 4))
							imageCnt = imageCnt + 1
							if (imageCnt >= 2):
								imageData.append(imageWord)
								imageWord = 0
								imageCnt = 0
			# Add remaining Pixels, if there are any
			if (imageCnt > 0):
				imageData.append(imageWord)
			print(TemporaryImage)
		case ConversionType.RGB332:
			print(TemporaryImage)
		case ConversionType.RGB565:
			# Relatively easy here, just trim the 24-bit RGB image to 16-bit RGB
			# and add it to the list
			for x in range(TemporaryImage.width):
				for y in range(TemporaryImage.height):
					color = list(TemporaryImage.getpixel((x,y)))
					color[0] = color[0] & 0xF8	# R
					color[1] = color[1] & 0xFC	# G
					color[2] = color[2] & 0xF8	# B
					imageData.append((color[0] << 8) | (color[1] << 5) | (color[2]))
			print(TemporaryImage)
		case ConversionType.RGB888:
			# Fit the color into 24bit and add it to the list
			for x in range(TemporaryImage.width):
				for y in range(TemporaryImage.height):
					color = list(TemporaryImage.getpixel((x,y)))
					imageData.append((color[0] << 16) | (color[1] << 8) | (color[2]))
	
	# compress the data if requested
	if (compression):
		imageData = rle_compress(imageData)

	# Write down the image size into the header
	imageHeader[ImageH.IMAGESIZE] = len(imageData)

	# Return the image header, color table and image data
	return imageHeader,colorTable,imageData

#ToDo: Indexing option window
def open_window():
    layout = [[sg.Text("New Window", key="new")]]
    window = sg.Window("Second Window", layout, modal=True)
    choice = None
    while True:
        event, values = window.read()
        if event == "Exit" or event == sg.WIN_CLOSED:
            break
        
    window.close()

def main():
	menubar_layout = [
		['&File', ['&Open','&Quit']],
		['&Help', ['&About']]
	]

	leftCol_layout = [
		[sg.Frame('Colour Settings', [
			[sg.Radio('Custom', 1, key='-RB_COL_CUS-', enable_events=True, disabled=True), sg.Button('Configure', key='-BTN_CONFIG-', expand_x=True, disabled=False)],
			[sg.Radio('1bpp (Monochrome B/W)', 1, key='-RB_COL_1BM-', enable_events=True)],
			[sg.Radio('4bpp (16 fixed Colors', 1, key='-RB_COL_4C-', enable_events=True)],
			[sg.Radio('8bpp (256 Colors - RGB332)', 1, key='-RB_COL_8B-', enable_events=True)],
			[sg.Radio('16bpp (64k Colors - RGB565)', 1, key='-RB_COL_16B-', enable_events=True)],
			[sg.Radio('24bpp (RGB888)', 1, key='-RB_COL_24B-', enable_events=True, default=True)]],
			expand_x=True)
		],
		[sg.Frame('Compression Type', [
			[sg.Radio('No Compression at all', 2, key='-RB_COMP_NO-', default=True)],
			[sg.Radio('Basic RLE Compression', 2, key='-RB_COMP_RLE-')]],
			expand_x=True)
		],
		[sg.Frame('Dithering Settings', [
			[sg.Radio('No dithering applied', 3, key='-RB_DIT_NO-', default=True)],
			[sg.Radio('Floyd-Steinberg-Dithering', 3, key='-RB_DIT_FS-', disabled=True)]],
			expand_x=True)
		],
		[sg.Frame('Image Resize', [
		#	[sg.Radio('Pixel', 4, key='-RB_RES_PXL-', enable_events=True, default=True),sg.Radio('Percentage', 4, key='-RB_RES_PRC-', enable_events=True)],
			[sg.Text('W:'), sg.Input(key='-IN_SIZE_X-', size=(8,1), enable_events=True), sg.Text('H:'), sg.Input(key='-IN_SIZE_Y-', size=(8,1), enable_events=True)],
			[sg.Checkbox('Lock Ratio', key='-CB_RAT-', default=True)]],
			expand_x=True)
		],
		[sg.VPush()],
		[sg.Button('Preview', key='-BTN_PREV-', expand_x=True)]
	]	

	rightCol_layout = [
		[sg.Text(' ', key='-TBXPATH-', expand_x=True, justification='center')],
		[sg.Image(key='-IMGBOX-', expand_x=True, expand_y=True)],
		[sg.Text(size=(60,1))]
	]

	layout = [
		[sg.Menubar(menubar_layout)],
		[sg.Pane([
			sg.Column(leftCol_layout, expand_y=True, expand_x=False, element_justification='l'),
			sg.Column(rightCol_layout, element_justification='c', expand_y=True, expand_x=True)],
			orientation='h', relief=sg.RELIEF_SUNKEN, key='-PANE-')
		]
	]

	window = sg.Window('Image converter', layout, border_depth=0, resizable=True, finalize=True)
	window.bind('<Configure>', "WinEvent")
	window.set_min_size((window.size[0], window.size[1]+30))
	window['-PANE-'].expand(True, True, True)
	window['-IMGBOX-'].expand(True, True)
	window.bring_to_front()

	# Calculate the space between the window and the image control itself. Will be used to set the image's size
	ImageOffset = (window.size[0] - window['-IMGBOX-'].get_size()[0], window.size[1] - window['-IMGBOX-'].get_size()[1])

	OriginalImage = None	# Original Image, do not change
	ImageToDisplay = None	# Image to Display and manipulate
	ConvertedImage = None	# Result / Converted Image

	# Setup the threaded timer beforehand, used in TimerStrategy
	timer_windowResize = threading.Timer(THREADWAIT, resizeImage,args=(window, ImageToDisplay, ImageOffset))

	while True:
		events, values = window.read()

		# Quit Application
		if (events == sg.WIN_CLOSED or events == 'Quit'):
			break
			
		# Show preview of the current settings
		if (events == '-BTN_PREV-'):
			if (ImageToDisplay == None): continue
			conType = ConversionType.UNDEFINED
			if values['-RB_COL_CUS-'] == True:	conType = ConversionType.INDEXED
			if values['-RB_COL_1BM-'] == True:	conType = ConversionType.MONOCHROME
			if values['-RB_COL_4C-'] == True:	conType = ConversionType.RGB16C
			if values['-RB_COL_8B-'] == True:	conType = ConversionType.RGB332
			if values['-RB_COL_16B-'] == True:	conType = ConversionType.RGB565
			if values['-RB_COL_24B-'] == True:	conType = ConversionType.RGB888

			ImageToDisplay = convertImage(OriginalImage, ((int)(values['-IN_SIZE_X-']),(int)(values['-IN_SIZE_Y-'])), conType, values['-RB_DIT_FS-'])
			resizeImage(window, ImageToDisplay, ImageOffset)
			convertToPIF(OriginalImage, ((int)(values['-IN_SIZE_X-']),(int)(values['-IN_SIZE_Y-'])), conType, values['-RB_DIT_FS-'], values['-RB_COMP_RLE-'])

		# Open configuration
		if (events == '-BTN_CONFIG-'):
			open_window()

		# Open  image
		if (events == 'Open'):
			filename = sg.popup_get_file('Open Image', no_window=True, show_hidden=False, file_types=(("Images", "*.png *.gif *.bmp *.jpg *.jpeg"),))
			# Check if a image has been selected (aka popup returned a file path)
			if (filename):	
				OriginalImage = PIL.Image.open(filename).convert("RGB")
				ImageToDisplay = OriginalImage
				resizeImage(window, ImageToDisplay, ImageOffset)
				window['-RB_COL_24B-'].update(True)
				window['-RB_DIT_FS-'].update(disabled=True)
				window['-RB_DIT_NO-'].update(True)
				window['-TBXPATH-'].update(filename)
				window['-IN_SIZE_X-'].update(ImageToDisplay.size[0])
				window['-IN_SIZE_Y-'].update(ImageToDisplay.size[1])

		# Respond to window resize and re-load the image
		if (events == 'WinEvent'):
			if (not ImageToDisplay == None):
				# Strategy: Resize the image only when the resizing of the window has stopped, using a threated.timer in the background
				timer_windowResize.cancel()
				# Since canceling destroys the prepared threading.Timer setup, it has to be recreated - why no Threading.Timer.Reset?
				timer_windowResize = threading.Timer(THREADWAIT, resizeImage,args=(window, ImageToDisplay, ImageOffset))
				# (Re)start the timer to resize the image after THREADWAIT-amount of seconds
				timer_windowResize.start()

		# Disable Dithering for 16bpp or 24bpp option
		if ((events == '-RB_COL_16B-') or (events == '-RB_COL_24B-')):
			window['-RB_DIT_FS-'].update(disabled=True)
			window['-RB_DIT_NO-'].update(True)
		if ((events == '-RB_COL_CUS-') or (events == '-RB_COL_1BM-') or (events == '-RB_COL_4C-') or (events == '-RB_COL_8B-')):
			window['-RB_DIT_FS-'].update(disabled=False)

		# Radio button for Pixel has been (un)checked
		if (events == '-RB_RES_PXL-'):
			print(events, values['-RB_RES_PXL-'])
			if (not ImageToDisplay == None):
				imgXpx = int(ImageToDisplay.size[0])
				imgYpx = int(ImageToDisplay.size[1])	

				window['-IN_SIZE_X-'].update(imgXpx)
				window['-IN_SIZE_Y-'].update(imgYpx)

		# Radio Button for Percentage has been (un)checked
		if (events == '-RB_RES_PRC-'):
			print(events, values['-RB_RES_PRC-'])
			if (not ImageToDisplay == None):
				imgXpx = int(ImageToDisplay.size[0])
				imgYpx = int(ImageToDisplay.size[1])	

				window['-IN_SIZE_X-'].update(100)
				window['-IN_SIZE_Y-'].update(100)
	
		# Check pixel input if valid, and add ratio if required
		if (events == '-IN_SIZE_X-'):
			if (not ImageToDisplay == None):
				try:
					value = (int)(values['-IN_SIZE_X-'])
					if (values['-CB_RAT-'] == True):
						window['-IN_SIZE_Y-'].update(int(value * (OriginalImage.size[1] / OriginalImage.size[0])))
				except:
					window['-IN_SIZE_X-'].update(int(OriginalImage.size[0]))

		# Check pixel input if valid, and add ratio if required
		if (events == '-IN_SIZE_Y-'):
			if (not ImageToDisplay == None):
				try:
					value = (int)(values['-IN_SIZE_Y-'])
					if (values['-CB_RAT-'] == True):
						window['-IN_SIZE_X-'].update(int(value / (OriginalImage.size[1] / OriginalImage.size[0])))
				except:
					window['-IN_SIZE_Y-'].update(int(OriginalImage.size[1]))
	window.close()

sg.theme('Default1')
if __name__ == "__main__":
	main()