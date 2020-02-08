import argparse
import hashlib
import os
from glob import glob

import cv2
import torch
from torch.autograd import Variable
from tqdm import tqdm

from ssd import build_ssd, BaseTransform


def detect_face(image, network, transformer, device, threshold=0.35):
    SIZE_THRESH = 600
    x = torch.from_numpy(transformer(image)[0]).permute(2, 0, 1)
    x = Variable(x.unsqueeze(0)).to(device)
    y = network(x)
    detections = y.data
    scale = torch.Tensor([image.shape[1], image.shape[0],
                          image.shape[1], image.shape[0]])
    bboxes = []
    j = 0
    while detections[0, 1, j, 0] > threshold:
        pt = (detections[0, 1, j, 1:] * scale).cpu().numpy()
        x1, y1, x2, y2 = pt
        if x2 - x1 < SIZE_THRESH and y2 - y1 < SIZE_THRESH:
            bboxes.append((x1, y1, x2, y2))
        j += 1
    return bboxes


def get_files(input_dir, hash_file=None):
    video_ext = ['.mp4', '.mov']
    videos = [glob(f"{input_dir}/**/*{e}", recursive=args.recursive) for e in video_ext]
    # Reduce the 2d list from before to a 1d list
    videos = [vid for subvid in videos for vid in subvid]

    hashes = []
    for video in videos:
        hasher = hashlib.md5()
        buf_size = 65536
        with open(video, 'rb') as video_file:
            buf = video_file.read(buf_size)
            while len(buf) > 0:
                hasher.update(buf)
                buf = video_file.read(buf_size)
        hashes.append(hasher.hexdigest())

    if hash_file is None:
        return list(zip(videos, hashes))
    else:
        return_vids = []
        return_hashes = []
        processed_hashes = []
        with open(hash_file, 'r') as f:
            processed_hashes = [line.strip() for line in f]
        # Filter out the videos that have already been processed
        return [(file, file_hash) for (file, file_hash) in zip(videos, hashes) if file_hash not in processed_hashes]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract faces in data to label at a later time using a neural net trained to detect faces")
    parser.add_argument("--input_dir", default="data", type=str, help="The directory holding the video")
    parser.add_argument('-R', '--recursive', default=False, type=bool, help="Search the input dir recursively")
    parser.add_argument('--output_dir', default='data/faces', type=str, help="Where to output the extracted faces")
    parser.add_argument('--hash_file', default=None,
                        help='File containing md5 hashes of videos that have been processed')
    parser.add_argument('--trained_model', default='ssd300_WIDER_100455.pth', type=str, help="Trained state_dict file")
    parser.add_argument('--rate', default=10, type=int, help="Run the network on 1/rate frames in the video")
    parser.add_argument('--cuda', default=False, type=bool,
                        help='Run the neural network with cuda enabled (will be disabled if cuda isn\'t available')
    args = parser.parse_args()

    if args.cuda and torch.cuda.is_available():
        torch.set_default_tensor_type('torch.cuda.FloatTensor')
        device = torch.device('cuda:0')
    else:
        torch.set_default_tensor_type('torch.FloatTensor')
        device = torch.device('cpu')

    net = build_ssd('test', 300, 2)
    net.load_state_dict(torch.load(args.trained_model, map_location=device))
    net.eval()

    transformer = BaseTransform(net.size, (104, 117, 123))
    # This lets us break the generation up into different sessions if we want
    print("Finding files")
    files_and_hashes = get_files(args.input_dir, args.hash_file)
    print("Processing files")

    try:
        # Try to find the most recently written file (let us pick up where we left off)
        file_num = max(
            [int(name.split('.')[0]) for name in glob(f'{args.output_dir}*.jpg') if name.split('.')[0].isnumeric()])
    except ValueError as ex:
        # Looks like there's no other labeled files
        file_num = 0
    hash_file = args.hash_file
    if hash_file is None:
        hash_file = "processed_hashes.txt"
    with open(hash_file, "w+") as hash_out:
        for video_file, file_hash in files_and_hashes:
            print(f"Opening {video_file} :: {file_hash}")
            video = cv2.VideoCapture(video_file)
            file_len = int(video.get(cv2.CAP_PROP_FRAME_COUNT))

            for frame_num in tqdm(range(file_len)):
                ret, frame = video.read()

                if frame_num % args.rate == 0:
                    frame = cv2.flip(frame, 1)
                    boxes = detect_face(frame, net, transformer, device)
                    for box in boxes:
                        x1, y1, x2, y2 = box
                        # Might have to swap the x and y
                        face_file_name = os.path.join(args.output_dir, f'{file_num}.jpg')
                        cv2.imwrite(face_file_name, frame[y1:y2, x2:x2])
                        file_num += 1

            video.release()
            hash_out.write(f"{file_hash}\n")