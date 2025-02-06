from typing import Union
from fastapi import FastAPI
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
import boto3
from botocore.exceptions import NoCredentialsError
import os
from dotenv import load_dotenv
import time
import json
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI()

# ✅ Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Allow requests from React frontend
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all headers
)

# Load environment variables from .env file
load_dotenv()

# AWS Credentials (Set via environment variables or hardcoded - Not recommended)
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")
BUCKET_NAME = "transcription-input-folder"
OUTPUT_BUCKET_NAME = "transcription-output-folder"
AWS_REGION = "ap-south-1"

# Initialize S3 client
s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
)

transcribe_client = boto3.client(
    "transcribe",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name='ap-south-1'
)


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: Union[str, None] = None):
    return {"item_id": item_id, "q": q}

@app.post("/upload/media/")
async def upload_file(file: UploadFile = File(...)):
    """
    Upload a video or audio file to S3.
    """
    try:
        # Read file content
        file_content = await file.read()

        # Define S3 object key (filename in S3)
        s3_object_key = f"uploads/{file.filename}"

        # Upload to S3
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_object_key,
            Body=file_content,
            ContentType=file.content_type
        )

        return {
            "message": "File uploaded successfully",
            "file_url": f"https://{BUCKET_NAME}.s3.amazonaws.com/{s3_object_key}"
        }
    
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS Credentials not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))    

def convert_to_srt(transcription_json):
    """
    Convert AWS Transcribe JSON output to SRT format.
    """
    srt_output = []
    counter = 1

    for item in transcription_json['results']['items']:
        if 'start_time' in item and 'end_time' in item:
            start_time = float(item['start_time'])
            end_time = float(item['end_time'])
            content = item['alternatives'][0]['content']

            # Convert time to SRT format
            start_time_srt = time.strftime('%H:%M:%S', time.gmtime(start_time)) + f",{int((start_time % 1) * 1000)}"
            end_time_srt = time.strftime('%H:%M:%S', time.gmtime(end_time)) + f",{int((end_time % 1) * 1000)}"

            # Append formatted subtitle entry
            srt_output.append(f"{counter}\n{start_time_srt} --> {end_time_srt}\n{content}\n")
            counter += 1

    return "\n".join(srt_output)


@app.post("/transcribe/")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form("en-US")
    ):
    """
    Upload a .wav file to S3, process it with AWS Transcribe, and return subtitles in .srt format.
    """
    # if not file.filename.endswith(".wav"):
    #     raise HTTPException(status_code=400, detail="Only .wav files are supported.")

    try:
        # Upload the file to S3
        file_content = await file.read()
        s3_object_key = f"uploads/{file.filename}"

        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_object_key,
            Body=file_content,
            ContentType="audio/wav"
        )

        # Start AWS Transcribe Job
        job_name = f"transcription-{int(time.time())}"
        file_uri = f"s3://{BUCKET_NAME}/{s3_object_key}"

        transcribe_client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={"MediaFileUri": file_uri},
            MediaFormat="wav",
            LanguageCode=language,
            OutputBucketName=OUTPUT_BUCKET_NAME,
            Subtitles = {
                'Formats': [
                    'vtt','srt'
                 ],
            'OutputStartIndex': 1 
            }
        )

        # Wait for transcription job to complete
        while True:
            response = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
            job_status = response["TranscriptionJob"]["TranscriptionJobStatus"]

            if job_status in ["COMPLETED", "FAILED"]:
                break
            time.sleep(5)  # Wait before checking again

        if job_status == "FAILED":
            raise HTTPException(status_code=500, detail="Transcription failed.")

        # Retrieve Transcription Output
        transcript_uri = response["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
        transcript_json = s3_client.get_object(Bucket=OUTPUT_BUCKET_NAME, Key=transcript_uri.split(f"{OUTPUT_BUCKET_NAME}/")[-1])
        transcript_data = json.loads(transcript_json["Body"].read().decode("utf-8"))

        # ✅ Retrieve `.srt` File URL from S3
        subtitle_key = f"{job_name}.srt"  # AWS Transcribe saves subtitles with this name
        srt_file_url = f"https://{OUTPUT_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{subtitle_key}"

        return {"message": "Transcription successful", "srt_file_url": srt_file_url}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))