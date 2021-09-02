from numpy.core.numeric import ones
from compositeKRR import CompositeKernelRegression, SingleLayerKRR, RLS2, DeepKernelRegression
import math
import torch
import torch.nn as nn
import torchviz
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import gpytorch as gpy
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms
from PIL import Image
from wand.image import Image, Color
from itertools import chain

def remap( x, oMin, oMax, nMin, nMax ):
	
    #range check
    if oMin == oMax:
        print("Warning: Zero input range")
        return None

    if nMin == nMax:
        print("Warning: Zero output range")
        return None

    #check reversed input range
    reverseInput = False
    oldMin = min( oMin, oMax )
    oldMax = max( oMin, oMax )
    if not oldMin == oMin:
        reverseInput = True

    #check reversed output range
    reverseOutput = False   
    newMin = min( nMin, nMax )
    newMax = max( nMin, nMax )
    if not newMin == nMin :
        reverseOutput = True

    portion = (x-oldMin)*(newMax-newMin)/(oldMax-oldMin)
    if reverseInput:
        portion = (oldMax-x)*(newMax-newMin)/(oldMax-oldMin)

    result = portion + newMin
    if reverseOutput:
        result = newMax - portion

    return result
def quad_as_rect(quad):
    if quad[0] != quad[2]: return False
    if quad[1] != quad[7]: return False
    if quad[4] != quad[6]: return False
    if quad[3] != quad[5]: return False
    return True

def quad_to_rect(quad):
    assert(len(quad) == 8)
    assert(quad_as_rect(quad))
    return (quad[0], quad[1], quad[4], quad[3])

def rect_to_quad(rect):
    assert(len(rect) == 4)
    return (rect[0], rect[1], rect[0], rect[3], rect[2], rect[3], rect[2], rect[1])

def shape_to_rect(shape):
    assert(len(shape) == 2)
    return (0, 0, shape[0], shape[1])

def griddify(rect, w_div, h_div):
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    x_step = w / float(w_div)
    y_step = h / float(h_div)
    y = rect[1]
    grid_vertex_matrix = []
    for _ in range(h_div + 1):
        grid_vertex_matrix.append([])
        x = rect[0]
        for _ in range(w_div + 1):
            grid_vertex_matrix[-1].append([int(x), int(y)])
            x += x_step
        y += y_step
    grid = np.array(grid_vertex_matrix)
    return grid

def distort_grid(org_grid, max_shift):
    new_grid = np.copy(org_grid)
    x_min = np.min(new_grid[:, :, 0])
    y_min = np.min(new_grid[:, :, 1])
    x_max = np.max(new_grid[:, :, 0])
    y_max = np.max(new_grid[:, :, 1])
    new_grid += np.random.randint(- max_shift, max_shift + 1, new_grid.shape)
    new_grid[:, :, 0] = np.maximum(x_min, new_grid[:, :, 0])
    new_grid[:, :, 1] = np.maximum(y_min, new_grid[:, :, 1])
    new_grid[:, :, 0] = np.minimum(x_max, new_grid[:, :, 0])
    new_grid[:, :, 1] = np.minimum(y_max, new_grid[:, :, 1])
    return new_grid

def grid_to_mesh(src_grid, dst_grid):
    assert(src_grid.shape == dst_grid.shape)
    mesh = []
    for i in range(src_grid.shape[0] - 1):
        for j in range(src_grid.shape[1] - 1):
            src_quad = [src_grid[i    , j    , 0], src_grid[i    , j    , 1],
                        src_grid[i + 1, j    , 0], src_grid[i + 1, j    , 1],
                        src_grid[i + 1, j + 1, 0], src_grid[i + 1, j + 1, 1],
                        src_grid[i    , j + 1, 0], src_grid[i    , j + 1, 1]]

            dst_quad = [dst_grid[i    , j    , 0], dst_grid[i    , j    , 1],
                        dst_grid[i + 1, j    , 0], dst_grid[i + 1, j    , 1],
                        dst_grid[i + 1, j + 1, 0], dst_grid[i + 1, j + 1, 1],
                        dst_grid[i    , j + 1, 0], dst_grid[i    , j + 1, 1]]
            dst_rect = quad_to_rect(dst_quad)
            mesh.append([dst_rect, src_quad])
    return mesh

def train_loop(data_x, data_y, model, loss_fn, optimizer, num_epochs=100, is_neg_loss=0, scheduler=None):
    threshold = 1

    print('training_epochs: ', num_epochs)
    prev_loss = 0;
    for epoch in range(num_epochs):

      # Backpropagation
      optimizer.zero_grad()
      # Compute prediction and loss
      pred = model(data_x)
      loss = loss_fn(pred, data_y)

      if is_neg_loss:
          loss = -1*loss
      is_last_epoch = (num_epochs - 1 ) == epoch or (loss < threshold)

      if torch.abs(prev_loss - loss) < 0.00000001:
          print('converged!')
          break
      prev_loss = loss

      loss.backward()
      optimizer.step()

      if scheduler:
          print('scheduler step')
          scheduler.step()


      if epoch % (1000 + 0*num_epochs) == 0:
        loss = loss.item()
        print(f"{ epoch } loss: {loss:>7f}]")

      if(is_last_epoch):
          return True


def createSyntheticData(num_data_points=10):
    #following the defination specified in the paper.
    input_domain = {'x_0': [-1, 1],
             'x_1': [-1, 1]}

    def test_fn_1(x_0, x_1):
      return (0.1 + torch.abs(x_0 - x_1))**(-1)

    def test_fn_2(x_0, x_1):
      return 1 if (x_0*x_1 > 3/20) else 0

    # initializing the input domains
    x_0 = torch.linspace(input_domain['x_0'][0], input_domain['x_0'][1],num_data_points)
    x_1 = torch.linspace(input_domain['x_1'][0], input_domain['x_1'][1],num_data_points)

    #initializing the grid and outputs
    x_grid= []
    output_1 = []
    output_2 = []


    for x in x_0:
      out_1_row = []
      out_2_row = []
      for y in x_1:
        # calculating the ouptuts
        out_1 = test_fn_1(x, y)
        out_2 = test_fn_2(x, y)
        # appending the input values and fn outputs.
        x_grid.append([x,y])
        out_1_row.append(out_1)
        out_2_row.append(out_2)
      output_1.append(out_1_row)
      output_2.append(out_2_row)

    # converting the inputs and outputs to torch tensor with appropriate dimensions.
    x_grid = torch.tensor(x_grid).reshape(num_data_points**2, 2)
    output_1 = torch.tensor(output_1).reshape(num_data_points**2, 1)
    output_2 = torch.tensor(output_2).reshape(num_data_points**2, 1)

    domains = [2, 2]
    ranges  = [2, 1]

    return [domains, ranges, x_grid, output_1, output_2]

def e2eSKRR(data_x, data_y, device, num_epochs=100):
    # TODO: create single layer KRR

    learning_rate = 0.0005

    # data_to_device
    data_x = data_x.to(device)
    data_y = data_y.to(device)

    likelihood = gpy.likelihoods.GaussianLikelihood()
    model = SingleLayerKRR(data_x,data_y, likelihood).to(device)

    print('SKRR params: ', model.parameters())

    loss = gpy.mlls.ExactMarginalLogLikelihood(likelihood, model)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    decayRate = 0.96
    lr_scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer=optimizer, gamma=decayRate)
    # Find optimal model hyperparameters
    model.train()
    likelihood.train()
    data_y = data_y.squeeze(1)
    train_loop(data_x, data_y, model, loss, optimizer,num_epochs, is_neg_loss=1,  scheduler=lr_scheduler )

    predY = model(data_x)
    print('predY from SKRR: ', predY)

    return model

def repr_fig6_plotly(num_data_points=10, num_epochs=20000):
    # check for cuda
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    device = 'cpu'
    print('Using {} device'.format(device))

    _,ranges,data_x,data_y_h1, data_y_h2 = createSyntheticData(num_data_points)
    data_x = data_x.to(device)
    data_y_h1 = data_y_h1.to(device)
    data_y_h2 = data_y_h2.to(device)*1.0
    data_y_h1 = data_y_h1.to(device)*1.0

    # calculating the models for constructing h1 and h2 functions.
    first_layer_poly_kernel_degree = 2
    model_comp_h1_v1 = e2eKRR(data_x, data_y_h1, ranges, first_layer_poly_kernel_degree, device, num_epochs, retain_layer_outputs=True);
    layer_outputs = model_comp_h1_v1.layer_outputs

    l0_out = layer_outputs[0].detach() # layer 0 outputs
    l0_out = l0_out.cpu().detach().numpy()

    pts = np.loadtxt(np.DataSource().open('https://raw.githubusercontent.com/plotly/datasets/master/mesh_dataset.txt'))
    x, y, z = pts.T

    x = data_x[:, 0].cpu().detach().numpy()
    y = data_x[:, 1].cpu().detach().numpy()
    z = data_y_h1[:,0].cpu().detach().numpy()
    intensity = remap(z, np.min(z), np.max(z), 0.0, 1.0)

    l0_x = l0_out[:, 0]
    l0_y = l0_out[:, 1]


    fig = make_subplots(
    rows=1, cols=2,
    specs=[[{'type': 'mesh3d'}, {'type': 'mesh3d'}],
           ])

    mesh_plot_00 = go.Mesh3d(x=x, y=y, z=z,
                    alphahull=5,
                    opacity=1.0,
                    color='cyan',
                    colorscale=[[0.0, 'blue'],
                                [0.5, 'magenta'],
                                [1.0, 'green']],
                    intensity = intensity
                    )



    mesh_plot_01= go.Mesh3d(x=l0_x, y=l0_y, z=z,
                    alphahull=5,
                    opacity=1.0,
                    color='cyan',
                    colorscale=[[0.0, 'blue'],
                                [0.5, 'magenta'],
                                [1.0, 'green']],
                    intensity = intensity
                    )

    fig.add_trace(mesh_plot_00, row=1, col=1)
    fig.add_trace(mesh_plot_01, row=1, col=2)

    fig.update_layout(
        title_text='Representation learning in deep kernel ridge regression',
        height=800,
        width=800*2
    )
    fig.show()


def repr_fig6_exp(num_data_points=10, num_epochs=10000):
    # check for cuda
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    device = 'cpu'
    print('Using {} device'.format(device))

    _,ranges,data_x,data_y_h1, data_y_h2 = createSyntheticData(num_data_points)
    data_x = data_x.to(device)
    data_y_h1 = data_y_h1.to(device)
    data_y_h2 = data_y_h2.to(device)*1.0

    # calculating the models for constructing h1 and h2 functions.
    first_layer_poly_kernel_degree = 2
    model_comp_h1_v1 = e2eKRR(data_x, data_y_h1, ranges, first_layer_poly_kernel_degree, device, num_epochs, retain_layer_outputs=True);
    layer_outputs = model_comp_h1_v1.layer_outputs

    l0_out = layer_outputs[0].detach() # layer 0 outputs
    l0_out = l0_out.cpu().detach().numpy()

    # convert data_y into rgb image data
    data_y_h1 = data_y_h1.squeeze(1)
    data_y_h2 = data_y_h2.squeeze(1)
    data_y_h1_grid = np.reshape(data_y_h1, [num_data_points, num_data_points, 1])
    data_y_rgb = np.concatenate([data_y_h1_grid, data_y_h1_grid, data_y_h1_grid], 2)

    # convert array into image
    img = Image.from_array(data_y_rgb)

    # image utils for easier viewing.
    img.background_color = Color('skyblue')
    img.virtual_pixel = 'background'
    dist_img = img.clone()
    print('l0: ', l0_out, torch.min(data_x), torch.max(data_x))


    bins = num_data_points
    # NOTE: here, first index = column and second index traverse rows

    # source and dest points for experiment
    source_points = (
        (0, 0),
        (0, bins),
        (bins, 0),
        (bins, bins)
    )
    destination_points = (
        (0,0),
        (0 ,bins+2),
        (bins, 0),
        (bins, bins)
    )

    # source points = input data.
    # destination points = input data after rkhs layer to see how this layer morph the input data.
    data_x = data_x.cpu().detach().numpy()
    data_x_new = remap(data_x, np.min(data_x), np.max(data_x), 0, num_data_points)
    l0_out = remap(l0_out, np.min(l0_out), np.max(l0_out), np.min(l0_out)*(num_data_points/2), np.max(l0_out)*(num_data_points/2)) + num_data_points/2
    source_points = data_x_new
    destination_points = l0_out

    print('data_x_new', data_x, np.min(l0_out), np.max(l0_out))

    # gives the effect of zoom and pan.
    #dist_img.artifacts['distort:viewport'] = '500x500-'+str(np.min(l0_out))+'-'+str(np.max(l0_out))
    dist_img.artifacts['distort:viewport'] = '500x500-250-250'

    # prepare the args and distorting.
    order = chain.from_iterable(zip(source_points, destination_points))
    dist_args = list(chain.from_iterable(order))
    dist_img.distort('bilinear_forward', dist_args)

    #plotting...
    fig, axs = plt.subplots(2,2)
    axs[0,0].imshow(data_y_rgb)
    axs[0,1].imshow(dist_img)
    plt.show()


def repr_fig6(num_data_points=10, num_epochs=10000):
    # check for cuda
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    device = 'cpu'
    print('Using {} device'.format(device))

    _,ranges,data_x,data_y_h1, data_y_h2 = createSyntheticData(num_data_points)
    data_x = data_x.to(device)
    data_y_h1 = data_y_h1.to(device)
    data_y_h2 = data_y_h2.to(device)*1.0

    # calculating the models for constructing h1 and h2 functions.
    first_layer_poly_kernel_degree = 2
    model_comp_h1_v1 = e2eKRR(data_x, data_y_h1, ranges, first_layer_poly_kernel_degree, device, num_epochs, retain_layer_outputs=True);

    layer_outputs = model_comp_h1_v1.layer_outputs
    #print(layer_outputs)

    l0 = layer_outputs[0].detach()
    data_y_h1 = data_y_h1.squeeze(1)
    data_y_h2 = data_y_h2.squeeze(1)

    #data_y_h1 = data_y_h1.reshape([10, 10])
    mask = torch.abs(data_x - l0).less(1)
    mask = mask.sum(1).bool()
    masked_y_h1 = data_y_h1*mask


    l0_grid = l0.reshape([ int(data_x.shape[0]**(1/2)), int(data_x.shape[0]**(1/2)), data_x.shape[1]])

    print(data_y_h1.shape, data_y_h2.shape, data_y_h2.reshape(10, 10))

    data_x_grid = data_x.reshape([ int(data_x.shape[0]**(1/2)), int(data_x.shape[0]**(1/2)), data_x.shape[1]])
    dx1 = dx2 = data_x_grid[:, :, 1][:1, :].squeeze().cpu().detach().numpy()
    data_y_h1 = data_y_h1.reshape(data_x_grid.shape[:2])
    data_y_h1 = data_y_h1.cpu().detach().numpy() 
    data_y_h2 = data_y_h2.reshape(data_x_grid.shape[:2])
    data_y_h2 = data_y_h2.cpu().detach().numpy() 
    masked_y_h1 = masked_y_h1.reshape(data_x_grid.shape[:2])
    masked_y_h1 = masked_y_h1.cpu().detach().numpy() 


    print("dx1: ", dx1,"dx2: ", dx2,"data_y_h2: ", data_y_h2, 'masked_y1: ', masked_y_h1, 'l0: ', l0, l0_grid)
    #data_y_h2 = torch.ones_like(data_y_h2)

    fig, axs = plt.subplots(2,2)

    img = data_y_h1.copy()
    A = img.shape[0] / 3.0
    w = 2.0 / img.shape[1]


    shift = lambda x: A * np.sin(2.0*np.pi*x * w)

    print(img[:,0], img.shape)

    for i in range(img.shape[0]):
        img[:,i] = np.roll(img[:,i], int(shift(i)))

    axs[0,0].imshow(data_y_h1)
    axs[0,1].imshow(data_y_h2)
    axs[1,0].imshow(masked_y_h1)
    axs[1,1].imshow(img)


    plt.subplot_tool()
    #plt.show()

    bins = num_data_points

    im = Image.fromarray(data_y_h1)
    dst_grid = griddify(shape_to_rect(im.size), bins - 1, bins -1)

    l0min = l0.min(0).values
    l0max = l0.max(0).values

    linspace_x = np.linspace(l0min[0], l0max[0], bins)
    linspace_y = np.linspace(l0min[1], l0max[1], bins)
    catgor_x = np.digitize(l0[:, 0], linspace_x)
    catgor_y = np.digitize(l0[:, 1], linspace_y)
    catgor_x = np.expand_dims(catgor_x, 1)
    catgor_y = np.expand_dims(catgor_y, 1)

    print(linspace_x.shape, catgor_x.shape, catgor_y.shape)
    catgor = np.concatenate([catgor_x, catgor_y], 1)
    catgor_grid = catgor.reshape([ int(bins**(1)), int(bins**(1)), data_x.shape[1]])

    # converting category to index
    index_grid = catgor_grid - 1;
    index_grid = np.abs((index_grid >= 0)*index_grid)

    print('min&max: ', torch.min(l0_grid), torch.max(l0_grid), catgor.shape, np.min(dst_grid), dst_grid.shape, index_grid.shape)

    # TODO: Convert category to index and map them to dst_grid.
    # this will give us the grid coordinates of the transformed( transformed by our network layer ) input data grid.

    #src_grid = distort_grid(dst_grid, 1)

    mesh = grid_to_mesh(index_grid, dst_grid)

    #print('dst_grid: ', dst_grid, 'src_grid: ', src_grid, 'mesh: ', mesh, len(mesh), len(mesh[8]), len(mesh[8][0]))
    im = im.transform(im.size, Image.MESH, mesh)
    #im = im.resize((200,200), Image.LINEAR)

    axs[1,1].imshow(np.array(im))
    plt.show()

def repr_fig3(num_data_points=5,num_epochs=10000, viz_prediction_only=False):
    """
    Reproducing the result shown in figure3

    Visualizing the loss or only predictions from 2 deep kernel architectures.
    """

    # check for cuda
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    #device = 'cpu'
    print('Using {} device'.format(device))


    # specify the data.
    _,ranges,data_x,data_y_h1, data_y_h2 = createSyntheticData(num_data_points)

    data_x = data_x.to(device)
    data_y_h1 = data_y_h1.to(device)
    data_y_h2 = data_y_h2.to(device)*1.0

    # num_epochs = 1000

    print(data_x.shape, data_y_h1.shape, data_y_h2.shape)


    # calculating the models for constructing h1 and h2 functions.
    first_layer_poly_kernel_degree = 1
    model_comp_h1_v1 = e2eKRR(data_x, data_y_h1, ranges, first_layer_poly_kernel_degree, device, num_epochs);
    model_comp_h2_v1 = e2eKRR(data_x, data_y_h2*1.0, ranges, first_layer_poly_kernel_degree, device, num_epochs);

    first_layer_poly_kernel_degree = 2
    model_comp_h1_v2 = e2eKRR(data_x, data_y_h1, ranges, first_layer_poly_kernel_degree, device, num_epochs);
    model_comp_h2_v2 = e2eKRR(data_x, data_y_h2, ranges, first_layer_poly_kernel_degree, device, num_epochs);

    model_single_h1 = e2eSKRR(data_x, data_y_h1, device, num_epochs);
    model_single_h2 = e2eSKRR(data_x, data_y_h2, device, num_epochs);

    # Calculate the predictions.
    pred_y_comp_h1_v1 = model_comp_h1_v1(data_x)
    pred_y_comp_h2_v1 = model_comp_h2_v1(data_x)
    pred_y_comp_h1_v2 = model_comp_h1_v2(data_x)
    pred_y_comp_h2_v2 = model_comp_h2_v2(data_x)

    pred_y_single_h1 = model_single_h1(data_x).mean
    pred_y_single_h2 = model_single_h2(data_x).mean

    # calculating the loss at each point.
    loss_comp_h1_v1 = torch.abs(pred_y_comp_h1_v1 - data_y_h1*viz_prediction_only)
    loss_comp_h2_v1 = torch.abs(pred_y_comp_h2_v1 - data_y_h2*viz_prediction_only)
    loss_comp_h1_v2 = torch.abs(pred_y_comp_h1_v2 - data_y_h1*viz_prediction_only)
    loss_comp_h2_v2 = torch.abs(pred_y_comp_h2_v2 - data_y_h2*viz_prediction_only)

    loss_single_h1 = torch.abs(pred_y_single_h1 - data_y_h1.squeeze(1)).unsqueeze(1)
    loss_single_h2 = torch.abs(pred_y_single_h2 - data_y_h2.squeeze(1)).unsqueeze(1)

    # preparing data for visualization.
    loss_comp_h1_v1_viz = loss_comp_h1_v1.reshape([num_data_points, num_data_points]).cpu().detach().numpy()
    loss_comp_h2_v1_viz = loss_comp_h2_v1.reshape([num_data_points, num_data_points]).cpu().detach().numpy()
    loss_comp_h1_v2_viz = loss_comp_h1_v2.reshape([num_data_points, num_data_points]).cpu().detach().numpy()
    loss_comp_h2_v2_viz = loss_comp_h2_v2.reshape([num_data_points, num_data_points]).cpu().detach().numpy()

    loss_single_h1_viz = loss_single_h1.reshape([num_data_points, num_data_points]).cpu().detach().numpy()
    loss_single_h2_viz = loss_single_h2.reshape([num_data_points, num_data_points]).cpu().detach().numpy()

    data_x_grid = data_x.reshape([ int(data_x.shape[0]**(1/2)), int(data_x.shape[0]**(1/2)), data_x.shape[1]])
    dx1 = dx2 = data_x_grid[:, :, 1][:1, :].squeeze().cpu().detach().numpy()

    fig = make_subplots(rows=2, cols=3,row_titles=['Function A', 'Function B'], subplot_titles=['original fn', '(p = 1)°Matern', ' (p = 2)°Matern'])
    fig.add_trace(go.Contour(z=loss_single_h1_viz,x=dx1,y=dx2), 1, 1)
    fig.add_trace(go.Contour(z=loss_single_h2_viz,x=dx1,y=dx2), 2, 1)

    fig.add_trace(go.Contour(z=loss_comp_h1_v1_viz,x=dx1,y=dx2), 1, 2)
    fig.add_trace(go.Contour(z=loss_comp_h2_v1_viz,x=dx1,y=dx2), 2, 2)
    fig.add_trace(go.Contour(z=loss_comp_h1_v2_viz,x=dx1,y=dx2), 1, 3)
    fig.add_trace(go.Contour(z=loss_comp_h2_v2_viz,x=dx1,y=dx2), 2, 3)

    fig.show()

def vizLossLandscape(loss, x, y, z):
    """
    dim(data_x) = 2
    """
    fig = make_subplots(rows=1, cols=2, specs=[[{'is_3d': True}, {'is_3d': True}]],
                        subplot_titles=['loss 1', 'loss 2'],)
    fig.add_trace(go.Surface(x=x, y=y, z=z, colorbar_x=-0.07), 1, 1)

    fig.show()


def main(num_epochs=100):
    # check for cuda
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print('Using {} device'.format(device))
    _,ranges,data_x,data_y = createSyntheticData()
    return e2eKRR( data_x, data_y, ranges, device, num_epochs)

def main2(num_epochs=100):
    # check for cuda
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    device = 'cpu'
    print('Using {} device'.format(device))
    _,ranges,data_x,data_y = createSyntheticData()

    # Training data is 100 points in [0,1] inclusive regularly spaced
    #data_x = torch.linspace(0, 1, 100)

    # True function is sin(2*pi*x) with Gaussian noise
    #data_y = torch.sin(data_x * (2 * math.pi)) + torch.randn(data_x.size()) * math.sqrt(0.04)

    print('data shape: ', data_x.shape, data_y.shape)
    return e2eSKRR(data_x,data_y,device,num_epochs)

# main2()
def e2eKRR( data_x, data_y, ranges, degree, device, num_epochs=100, retain_layer_outputs=False):

    # hyperparams
    learning_rate = 0.0005
    #num_epochs = 100000


    # data_to_device
    data_x = data_x.to(device)
    data_y = data_y.to(device)

    print('e2eKRR num_epochs: ', num_epochs)

    print('data_x.device: ', data_x.device, device)

    # initializing the main training loop components.
    #model = CompositeKernelRegression(ranges, data_x, device, degree=degree, retain_layer_outputs=retain_layer_outputs) # TODO: specify the args
    model = RLS2(ranges, data_x, device, degree=degree, retain_layer_outputs=retain_layer_outputs) # TODO: specify the args
    model = model.to(device)

    predY = model(data_x)

    #print(predY.sum().shape, model.named_parameters())

    print(model.parameters()[0].is_leaf, model.parameters()[1].is_leaf)

    #p = torchviz.make_dot(predY.sum(), params=dict({"K1_weights":model.parameters()[0], 
    #                                                "K2_weights": model.parameters()[1] }),
    #                      show_attrs=True, show_saved=True)

    #p.render('somefile'+str(degree)+'.gv', view=True)

    loss = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, weight_decay=1e-5)

    train_loop(data_x, data_y, model, loss, optimizer, num_epochs,is_neg_loss=0)

    #predY = model(data_x)

    #print(predY, data_y)

    #del data_x
    #del data_y
    #del model
    torch.cuda.empty_cache()
    return model

#repr_fig3()
#main(nEpochs)

# visualizng loss
#repr_fig3()

# visualizng prediction
#repr_fig3(viz_prediction_only=True)

# repr_fig6_exp()
repr_fig6_plotly()


