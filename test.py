from arguments import parse_args
args = parse_args()
f = open('res_args.object','wb')
import pickle
pickle.dump(args,f)
f.close()
