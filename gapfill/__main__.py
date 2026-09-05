import argparse
from pathlib import Path
import pandas as pd
from gapfill.core import Reconstructor, submission


def main():
    parser=argparse.ArgumentParser(description='NDVI reconstruction: training and batch submission')
    sub=parser.add_subparsers(dest='command',required=True)
    fit=sub.add_parser('train')
    fit.add_argument('--input',required=True)
    fit.add_argument('--output',default='models/ndvi')
    fit.add_argument('--iterations',type=int,default=650)
    fit.add_argument('--seed',type=int,default=42)
    predict=sub.add_parser('predict')
    predict.add_argument('--input',required=True)
    predict.add_argument('--output',default='submission.csv')
    predict.add_argument('--model',default='models/ndvi')
    predict.add_argument('--prediction-column',choices=['primary_ndvi_true','primary_ndvi_pred'],default='primary_ndvi_true')
    args=parser.parse_args()
    if args.command=='train':
        from gapfill.train import train
        train(args.input,args.output,args.iterations,args.seed)
    else:
        if Path(args.input).resolve()==Path(args.output).resolve():
            parser.error('Output must not overwrite input')
        result=submission(pd.read_csv(args.input),Reconstructor(args.model)).rename(columns={'primary_ndvi_pred':args.prediction_column})
        Path(args.output).parent.mkdir(parents=True,exist_ok=True)
        result.to_csv(args.output,index=False,encoding='utf-8',float_format='%.10f')
        print(f'Saved {len(result)} control predictions to {args.output}')


if __name__=='__main__':
    main()
